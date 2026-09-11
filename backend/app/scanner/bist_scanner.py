import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter

from sqlalchemy import func,select
from sqlalchemy.orm import Session

from app.analysis.pipeline import PipelineResult, analyze_frames
from app.config.settings import AppSettings
from app.journal.decision_journal import log_decision
from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.provider import MarketDataProvider
from app.market_data.yahoo_provider import YahooMarketDataProvider
from app.market_data.eodhd_provider import EodhdHistoricalProvider
from app.market_data.market_session import BistMarketSession
from app.market_data.twelvedata_provider import TwelveDataProvider
from app.market_data.hybrid_provider import HybridMarketDataProvider
from app.models import Analysis,Candle,Order,Position,ScanRun,Setting,Symbol,WatchlistItem
from app.portfolio.paper_broker import DuplicateOrderError,PaperBroker
from app.portfolio.portfolio_manager import ensure_portfolio,portfolio_summary,take_snapshot
from app.portfolio.risk_manager import size_position
from app.scanner.watchlist_manager import update_watchlist
from app.scanner.universe_builder import UniverseBuilder
from app.services.forward_test import ensure_forward_run,upsert_daily_summary
from app.services.ai_analyst import AIAnalyst
from app.research.v4 import strategy_config_snapshot

logger=logging.getLogger("SCANNER")
_SCAN_LOCK=threading.Lock()


def json_safe(value):
    if isinstance(value,Decimal):return float(value)
    if isinstance(value,datetime):return value.isoformat()
    if isinstance(value,dict):return {key:json_safe(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return [json_safe(item) for item in value]
    return value


def effective_settings(db:Session,config:AppSettings)->dict:
    keys=("watchlist_score","entry_score","risk_per_trade_pct","scan_interval_minutes","max_position_size_pct",
          "minimum_cash_reserve_pct","max_open_positions","min_rr","commission_rate","slippage_rate")
    values={key:getattr(config,key) for key in keys}
    for row in db.scalars(select(Setting)):
        if row.key in values: values[row.key]=type(values[row.key])(str(row.value["value"]))
    return values


def get_provider(config:AppSettings)->MarketDataProvider:
    if config.data_mode.lower()=="mock":return MockMarketDataProvider()
    name=config.market_data_provider.lower()
    if name=="yahoo":return YahooMarketDataProvider()
    if name=="eodhd":return EodhdHistoricalProvider(config.eodhd_api_token or "")
    if name=="twelvedata":return TwelveDataProvider(config.twelve_data_api_key or "")
    if name in {"hybrid","dual","twelvedata+yahoo","twelve+yahoo"}:
        return HybridMarketDataProvider(config.twelve_data_api_key or "", config.hybrid_price_tolerance_pct)
    raise ValueError(f"Bilinmeyen market data provider: {config.market_data_provider}")


class BistScanner:
    def __init__(self,db:Session,config:AppSettings,provider:MarketDataProvider|None=None,require_market_session:bool=True):
        self.db,self.config=db,config
        self.runtime=effective_settings(db,config)
        self.analysis_config=config.model_copy(update=self.runtime)
        self.provider=provider or get_provider(config)
        self.require_market_session=require_market_session
        self.universe=UniverseBuilder(self.provider,self.analysis_config)
        self.forward_run=None

    def _log(self,category,decision,reason,symbol=None,score=None,details=None):
        run=self.forward_run
        return log_decision(self.db,category,decision,reason,symbol,score,details,
            run_id=run.run_id if run else None,strategy_version=run.strategy_version if run else None,
            strategy_config_hash=run.strategy_config_hash if run else None)

    def _data(self,symbol:str)->dict[str,list]:
        frames={}
        for timeframe in ("1d","1h","15m"):
            try:frames[timeframe]=self.provider.get_candles(symbol,timeframe,220)
            except Exception as exc:
                self._log("DATA_PROVIDER_ERROR","NO_TRADE",f"{timeframe}: {exc}",symbol,details={"timeframe":timeframe,"provider":self.provider.name})
                raise
        return frames

    def _persist_candles(self,symbol:str,frames:dict[str,list],source:str)->None:
        def timestamp_key(value:datetime)->datetime:
            # SQLite returns timezone-aware columns as naive values. Comparing
            # naive UTC keys keeps repeated scans idempotent on every backend.
            if value.tzinfo is None:return value
            return value.astimezone(timezone.utc).replace(tzinfo=None)

        for timeframe,items in frames.items():
            existing={timestamp_key(value) for value in self.db.scalars(
                select(Candle.timestamp).where(Candle.symbol==symbol,Candle.timeframe==timeframe)
            ).all()}
            for candle in items[-220:]:
                key=timestamp_key(candle.timestamp)
                if key not in existing:
                    self.db.add(Candle(symbol=symbol,timeframe=timeframe,timestamp=candle.timestamp,open=candle.open,high=candle.high,
                        low=candle.low,close=candle.close,volume=candle.volume,source=source))
                    existing.add(key)

    def analyze_symbol(self,symbol:str,analysis_at:datetime|None=None):
        frames=self._data(symbol);source="mock" if self.config.data_mode=="mock" else self.provider.name
        result=analyze_frames(symbol,frames,self.analysis_config,source,analysis_at,self.require_market_session)
        details=json_safe(result.details)
        details["ai"]=AIAnalyst(self.config).analyze(symbol,result.score,result.decision,details)
        summary=portfolio_summary(self.db,self.config.initial_balance)
        assessment=self.universe.assess(symbol,frames,summary["portfolio_value"])
        analysis=Analysis(symbol=symbol,price=result.price,score=result.score,trend=result.trend,market_structure=result.market_structure,
            setup=result.setup,decision=result.decision,reason=result.reason,details=details,data_source=source,
            signal_candle_time=result.signal_candle_time,data_valid=result.funnel["data_valid"],
            run_id=self.forward_run.run_id if self.forward_run else None,
            strategy_version=self.forward_run.strategy_version if self.forward_run else None,
            strategy_config_hash=self.forward_run.strategy_config_hash if self.forward_run else None)
        try:
            self.db.add(analysis)
            if not self.db.scalar(select(Symbol.id).where(Symbol.ticker==symbol)):self.db.add(Symbol(ticker=symbol,name=symbol))
            self._persist_candles(symbol,frames,source);self.db.commit();self.db.refresh(analysis)
        except Exception:
            self.db.rollback()
            raise
        previous=self.db.get(WatchlistItem,symbol)
        item=update_watchlist(self.db,symbol,result.score,result.setup,result.reason,self.runtime["watchlist_score"],self.runtime["entry_score"],
            result.funnel["data_valid"],bool(details["analysis_complete"]),result.funnel["sufficient_history"],sum(c.volume for c in frames["15m"][-20:])>0,
            price=result.price,setup_quality=details.get("setup",{}).get("score"),trend=result.trend,structure=result.market_structure,
            support=details.get("levels",{}).get("support"),resistance=details.get("levels",{}).get("resistance"),
            rr=details.get("risk_reward"),run_id=self.forward_run.run_id if self.forward_run else None)
        if previous and item is None:self._log("WATCHLIST","REMOVED",result.reason,symbol,result.score)
        context=details.get("analysis_context",{})
        self._log("SIGNAL",result.decision,result.reason,symbol,result.score,{"data_source":source,
            "signal_candle_time":result.signal_candle_time.isoformat(),"universe_status":assessment.status,
            "daily_context_timestamp":context.get("daily_candle_time"),"hourly_context_timestamp":context.get("hourly_candle_time"),
            "trigger_15m_timestamp":context.get("entry_candle_time")})
        return analysis,result,assessment

    def _manage_positions(self):
        portfolio=ensure_portfolio(self.db,self.config.initial_balance)
        run=self.forward_run
        broker=PaperBroker(self.db,self.runtime["commission_rate"],self.runtime["slippage_rate"],self.config.intrabar_policy,
            run.run_id if run else None,run.strategy_version if run else None,run.strategy_config_hash if run else None)
        if self.config.data_mode=="live" and self.provider.name=="mock":return
        for position in list(self.db.scalars(select(Position).where(Position.status=="OPEN"))):
            try:
                candle=self.provider.get_candles(position.symbol,"15m",2)[-1]
                broker.evaluate_candle(portfolio,position,candle)
                self._log("POSITION","RE_EVALUATED",f"OHLC {candle.open}/{candle.high}/{candle.low}/{candle.close}",position.symbol)
            except Exception as exc:self._log("DATA_PROVIDER_ERROR","NO_TRADE",f"Pozisyon değişmedi: {exc}",position.symbol)

    def _try_entries(self,items,funnel:dict,allow_entries:bool=True)->None:
        portfolio=ensure_portfolio(self.db,self.config.initial_balance)
        run=self.forward_run
        broker=PaperBroker(self.db,self.runtime["commission_rate"],self.runtime["slippage_rate"],self.config.intrabar_policy,
            run.run_id if run else None,run.strategy_version if run else None,run.strategy_config_hash if run else None)
        market_open=BistMarketSession.from_config(self.config).is_open()
        config_matches=bool(run and run.strategy_config_hash==strategy_config_snapshot(self.analysis_config)["sha256"])
        entries_enabled=allow_entries and self.config.operation_mode=="LIVE_PAPER" and self.config.data_mode=="live" and self.provider.name!="mock" and market_open and bool(run and not run.paused) and config_matches
        if not entries_enabled:
            self._log("ENTRY_GATE","NO_NEW_ENTRY","Entry gate closed: provider/data/session/pause/config safety condition",
                details={"market_open":market_open,"paused":bool(run and run.paused),"provider":self.provider.name,
                    "mode":self.config.operation_mode,"config_match":config_matches})
        for analysis,result,assessment in sorted(items,key=lambda pair:pair[0].score,reverse=True):
            if analysis.decision!="POSSIBLE_ENTRY":continue
            if not entries_enabled:continue
            if not assessment.affordable:
                self._log("RISK","UNAFFORDABLE",assessment.reason,analysis.symbol,analysis.score);continue
            key=f"{run.run_id}:{analysis.symbol}:{analysis.setup}:{result.signal_candle_time.isoformat()}"
            if self.db.scalar(select(Order.id).where(Order.idempotency_key==key)):
                self._log("ORDER","DUPLICATE_BLOCKED",key,analysis.symbol,analysis.score);continue
            if self.db.scalar(select(Position.id).where(Position.symbol==analysis.symbol,Position.status=="OPEN")):
                self._log("ORDER","DUPLICATE_BLOCKED","Açık pozisyon zaten var",analysis.symbol,analysis.score);continue
            setup=analysis.details["setup"];summary=portfolio_summary(self.db,self.config.initial_balance)
            risk=size_position(summary["portfolio_value"],summary["cash_balance"],Decimal(str(setup["entry_area"])),Decimal(str(setup["invalidation_level"])),
                Decimal(str(setup["target"])),self.runtime["risk_per_trade_pct"],self.runtime["max_position_size_pct"],
                self.runtime["minimum_cash_reserve_pct"],summary["open_positions"],self.runtime["max_open_positions"],self.runtime["min_rr"],
                Decimal(str(analysis.details["volatility"]["atr"])),self.config.minimum_stop_atr_pct,self.config.maximum_stop_atr_pct)
            if not risk.approved:self._log("RISK","NO_TRADE",risk.reason,analysis.symbol,analysis.score);continue
            funnel["risk_pass"]+=1
            try:
                broker.buy(portfolio,analysis.symbol,risk.quantity,analysis.price,Decimal(str(setup["invalidation_level"])),Decimal(str(setup["target"])),
                    analysis.setup,analysis.score,analysis.reason,key,result.signal_candle_time)
                funnel["buy"]+=1;self._log("ORDER","VIRTUAL_BUY",f"{risk.quantity} lot @ {analysis.price}",analysis.symbol,analysis.score)
            except DuplicateOrderError:self._log("ORDER","DUPLICATE_BLOCKED",key,analysis.symbol,analysis.score)

    def _run_cycle(self,max_symbols:int|None=None,closed_candle_timestamp:datetime|None=None)->dict:
        started=datetime.now(timezone.utc);clock=perf_counter();self.forward_run=ensure_forward_run(self.db,self.config)
        symbols=self.universe.candidates(max_symbols)
        funnel={"total":len(symbols),"data_valid":0,"sufficient_history":0,"htf_bullish":0,"valid_setup":0,"score_pass":0,"rr_pass":0,"risk_pass":0,"buy":0}
        run=ScanRun(started_at=started,provider=self.provider.name,data_mode=self.config.data_mode,total_symbols=len(symbols),valid_symbols=0,failed_symbols=0,stale_symbols=0,funnel={},errors=[],
            run_id=self.forward_run.run_id,timeframe="15m",closed_candle_timestamp=closed_candle_timestamp)
        self.db.add(run);self.db.commit();self._log("SCANNER","STARTED",f"{len(symbols)} sembol")
        self._manage_positions();items=[];errors=[]
        for symbol in symbols:
            try:
                analysis,result,assessment=self.analyze_symbol(symbol);items.append((analysis,result,assessment))
                for key in ("data_valid","sufficient_history","htf_bullish","valid_setup","score_pass","rr_pass"):funnel[key]+=int(result.funnel[key])
                if not result.funnel["data_valid"]:run.stale_symbols+=1
            except Exception as exc:
                # A failed flush/commit poisons the SQLAlchemy transaction until
                # rollback. One bad symbol must not prevent the remaining scan.
                self.db.rollback()
                errors.append({"symbol":symbol,"error":str(exc)});logger.exception("symbol_failed",extra={"symbol":symbol})
        self._try_entries(items,funnel,allow_entries=not errors);take_snapshot(self.db,self.config.initial_balance,self.forward_run.run_id)
        upsert_daily_summary(self.db,self.config,self.forward_run)
        run.completed_at=datetime.now(timezone.utc);run.duration_ms=round((perf_counter()-clock)*1000);run.valid_symbols=len(items);run.failed_symbols=len(errors);run.funnel=funnel;run.errors=errors
        run.watchlist_count=self.db.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.run_id==self.forward_run.run_id)) or 0
        run.signals=sum(item[0].decision=="POSSIBLE_ENTRY" for item in items);run.entries=funnel["buy"];self.db.commit()
        self._log("SCANNER","COMPLETED",f"{len(items)} analiz, {len(errors)} hata, {funnel['buy']} BUY",details=funnel)
        return {"status":"completed","analyzed":len(items),"errors":errors,"funnel":funnel,"duration_ms":run.duration_ms,
            "results":[{"symbol":a.symbol,"score":a.score,"decision":a.decision,"source":a.data_source,
                "universe_status":assessment.status} for a,_,assessment in sorted(items,key=lambda pair:pair[0].score,reverse=True)]}

    def run(self,max_symbols:int|None=None,closed_candle_timestamp:datetime|None=None)->dict:
        if self.config.operation_mode!="LIVE_PAPER":return {"status":"wrong_mode","analyzed":0,"errors":[],"results":[]}
        if not _SCAN_LOCK.acquire(blocking=False):return {"status":"already_running","analyzed":0,"errors":[],"results":[]}
        try:return self._run_cycle(max_symbols,closed_candle_timestamp)
        finally:_SCAN_LOCK.release()
