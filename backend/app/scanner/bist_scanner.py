import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter

from sqlalchemy import func,select
from sqlalchemy.orm import Session

from app.analysis.pipeline import PipelineResult, analyze_frames
from app.ai.groq_advisor import GroqAdvisor,attach_opinion,build_snapshot
from app.config.settings import AppSettings
from app.journal.decision_journal import log_decision
from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.provider import MarketDataProvider
from app.market_data.yahoo_provider import YahooMarketDataProvider
from app.market_data.eodhd_provider import EodhdHistoricalProvider
from app.market_data.market_session import BistMarketSession
from app.market_data.twelvedata_provider import TwelveDataProvider
from app.market_data.hybrid_provider import HybridMarketDataProvider
from app.models import Analysis,Candle,DecisionLog,MarketStateSnapshot,NewsItem,NewsMarketReaction,Order,Position,ScanRun,Setting,Symbol,WatchlistItem
from app.news.sentiment import GroqNewsAnalyzer
from app.portfolio.paper_broker import DuplicateOrderError,PaperBroker
from app.portfolio.portfolio_manager import ensure_portfolio,portfolio_summary,take_snapshot
from app.portfolio.risk_manager import size_position
from app.scanner.watchlist_manager import update_watchlist
from app.scanner.universe_builder import UniverseBuilder
from app.services.forward_test import ensure_forward_run,upsert_daily_summary
from app.services.telegram import TelegramNotifier
from app.research.v4 import strategy_config_snapshot
from app.market_data.cache import redact_secret
from app.market_memory.service import MarketMemoryService

logger=logging.getLogger("SCANNER")
_SCAN_LOCK=threading.Lock()


def safe_provider_error(exc: Exception) -> str:
    return redact_secret(str(exc))[:500] or type(exc).__name__


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
    def __init__(self,db:Session,config:AppSettings,provider:MarketDataProvider|None=None,require_market_session:bool=True,
                 analysis_mode:str="LIVE",market_open:bool|None=None,analysis_at:datetime|None=None):
        self.db,self.config=db,config
        self.runtime=effective_settings(db,config)
        self.analysis_config=config.model_copy(update=self.runtime)
        self.provider=provider or get_provider(config)
        self.analysis_mode=analysis_mode if analysis_mode in {"LIVE","ANALYSIS_ONLY"} else "LIVE"
        self.market_open=market_open
        self.analysis_at=analysis_at
        self.require_market_session=require_market_session and self.analysis_mode=="LIVE"
        self.universe=UniverseBuilder(self.provider,self.analysis_config)
        self.ai_advisor=GroqAdvisor(config)
        self.telegram=TelegramNotifier(config)
        self.news_advisor=GroqNewsAnalyzer(config)
        self._benchmark_daily=None
        self.forward_run=None

    def _log(self,category,decision,reason,symbol=None,score=None,details=None):
        run=self.forward_run
        return log_decision(self.db,category,decision,reason,symbol,score,details,
            run_id=run.run_id if run else None,strategy_version=run.strategy_version if run else None,
            strategy_config_hash=run.strategy_config_hash if run else None)

    def _telegram_once(self,key:str,text:str,symbol:str|None=None,score:int|None=None)->dict:
        if not self.telegram.configured:return {"status":"DISABLED_OR_UNCONFIGURED"}
        existing=self.db.scalar(select(DecisionLog.id).where(
            DecisionLog.category=="TELEGRAM",DecisionLog.reason==key
        ).limit(1))
        if existing:return {"status":"DEDUPED"}
        result=self.telegram.send(text)
        if result.get("status")=="SENT":
            self._log("TELEGRAM","SENT",key,symbol,score,details=result)
        else:
            self._log("TELEGRAM","FAILED",key,symbol,score,details=result)
        return result

    def _data(self,symbol:str)->dict[str,list]:
        frames={}
        for timeframe in ("1d","1h","15m"):
            try:frames[timeframe]=self.provider.get_candles(symbol,timeframe,220)
            except Exception as exc:
                self._log("DATA_PROVIDER_ERROR","NO_TRADE",f"{timeframe}: {safe_provider_error(exc)}",symbol,details={"timeframe":timeframe,"provider":self.provider.name})
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

    def _relative_strength(self, daily:list)->dict:
        try:
            if self._benchmark_daily is None:self._benchmark_daily=self.provider.get_candles("XU100","1d",40)
            benchmark=self._benchmark_daily
            def change(rows,days):
                return (rows[-1].close/rows[-days-1].close-1)*100 if len(rows)>days and rows[-days-1].close else None
            result={}
            for days in (1,5,20):
                stock,xu100=change(daily,days),change(benchmark,days)
                result[f"stock_return_{days}d"],result[f"xu100_return_{days}d"]=stock,xu100
                result[f"relative_strength_{days}d"]=stock-xu100 if stock is not None and xu100 is not None else None
            rs=result.get("relative_strength_20d")
            result["label"]="NO_DATA" if rs is None else "GÜÇLÜ" if rs>1 else "ZAYIF" if rs<-1 else "NÖTR"
            return result
        except Exception:
            return {"label":"NO_DATA","stock_return_1d":None,"xu100_return_1d":None,"relative_strength_1d":None,
                "stock_return_5d":None,"xu100_return_5d":None,"relative_strength_5d":None,
                "stock_return_20d":None,"xu100_return_20d":None,"relative_strength_20d":None}

    def analyze_symbol(self,symbol:str,analysis_at:datetime|None=None):
        frames=self._data(symbol);source="mock" if self.config.data_mode=="mock" else self.provider.name
        result=analyze_frames(symbol,frames,self.analysis_config,source,analysis_at,self.require_market_session)
        details=json_safe(result.details)
        market_open=self.market_open if self.market_open is not None else BistMarketSession.from_config(self.config).is_open(analysis_at)
        details.update({"analysis_mode":self.analysis_mode,"market_open":market_open,
            "source_candle_timestamp":result.signal_candle_time.isoformat(),
            "stale_session":self.analysis_mode=="ANALYSIS_ONLY" and not market_open,
            "entries_enabled":self.analysis_mode=="LIVE" and market_open})
        details["technical_score"]=result.score
        details["relative_strength"]=json_safe(self._relative_strength(frames["1d"]))
        news_rows=self.db.scalars(select(NewsItem).where(NewsItem.symbol==symbol).order_by(NewsItem.published_at.desc()).limit(5)).all()
        news_context=[{"title":row.title,"source":row.source,"ai_sentiment":row.ai_sentiment,
            "ai_importance":row.ai_importance,"ai_summary":row.ai_summary,"ai_risks":row.ai_risks} for row in news_rows]
        details["news"]={"status":"OK" if news_context else "NO_NEWS","items":news_context,
            "news_score":max((row.ai_importance or 0 for row in news_rows),default=None)}
        prior_states=self.db.scalars(select(MarketStateSnapshot).where(MarketStateSnapshot.symbol==symbol)
            .order_by(MarketStateSnapshot.timestamp.desc()).limit(5)).all()
        prior_reactions=self.db.execute(select(NewsItem.category,NewsMarketReaction.return_1d,
            NewsMarketReaction.abnormal_return_1d).join(NewsMarketReaction,NewsMarketReaction.news_id==NewsItem.id)
            .where(NewsMarketReaction.symbol==symbol).order_by(NewsMarketReaction.evaluated_at.desc()).limit(5)).all()
        details["market_memory"]=json_safe({"previous_snapshots":[{"timestamp":row.timestamp,"price":row.price,
            "trend":row.trend,"structure":row.market_structure,"score":row.technical_score} for row in prior_states],
            "observed_news_reactions":[{"category":category,"return_1d":return_1d,
                "abnormal_return_1d":abnormal} for category,return_1d,abnormal in prior_reactions],
            "observations_only":True})
        summary=portfolio_summary(self.db,self.config.initial_balance)
        assessment=self.universe.assess(symbol,frames,summary["portfolio_value"])
        details["universe"]={"status":assessment.status,"reason":assessment.reason,"affordable":assessment.affordable}
        analysis=Analysis(symbol=symbol,price=result.price,score=result.score,trend=result.trend,market_structure=result.market_structure,
            setup=result.setup,decision=result.decision,reason=result.reason,details=details,data_source=source,
            signal_candle_time=result.signal_candle_time,data_valid=result.funnel["data_valid"],
            run_id=self.forward_run.run_id if self.forward_run else None,
            strategy_version=self.forward_run.strategy_version if self.forward_run else None,
            strategy_config_hash=self.forward_run.strategy_config_hash if self.forward_run else None)
        opinion=self.ai_advisor.evaluate(build_snapshot(symbol,result.price,result.score,result.decision,details))
        opinion["combined_ai"]=self.news_advisor.combined(opinion,news_context)
        attach_opinion(analysis,opinion)
        try:
            self.db.add(analysis)
            if not self.db.scalar(select(Symbol.id).where(Symbol.ticker==symbol)):self.db.add(Symbol(ticker=symbol,name=symbol))
            self._persist_candles(symbol,frames,source)
            if self.config.market_memory_enabled:
                benchmark_price=self._benchmark_daily[-1].close if self._benchmark_daily else None
                MarketMemoryService(self.db,self.config).record_analysis(analysis,benchmark_price)
            self.db.commit();self.db.refresh(analysis)
        except Exception:
            self.db.rollback()
            raise
        previous=self.db.get(WatchlistItem,symbol)
        item=update_watchlist(self.db,symbol,result.score,result.setup,result.reason,self.runtime["watchlist_score"],self.runtime["entry_score"],
            result.funnel["data_valid"],bool(details["analysis_complete"]),result.funnel["sufficient_history"],sum(c.volume for c in frames["15m"][-20:])>0,
            price=result.price,setup_quality=details.get("setup",{}).get("score"),trend=result.trend,structure=result.market_structure,
            support=details.get("levels",{}).get("support"),resistance=details.get("levels",{}).get("resistance"),
            rr=details.get("risk_reward"),run_id=self.forward_run.run_id if self.forward_run else None,analysis_mode=self.analysis_mode)
        if previous and item is None:self._log("WATCHLIST","REMOVED",result.reason,symbol,result.score)
        context=details.get("analysis_context",{})
        self._log("AI_ADVISORY",opinion["status"],opinion.get("summary") or opinion["reason"],symbol,result.score,
            {"provider":opinion.get("provider"),"model":opinion.get("model"),"verdict":opinion.get("verdict"),
             "confidence":opinion.get("confidence"),"execution_authority":False})
        self._log("SIGNAL",result.decision,result.reason,symbol,result.score,{"data_source":source,
            "signal_candle_time":result.signal_candle_time.isoformat(),"universe_status":assessment.status,
            "daily_context_timestamp":context.get("daily_candle_time"),"hourly_context_timestamp":context.get("hourly_candle_time"),
            "trigger_15m_timestamp":context.get("entry_candle_time")})
        if self.analysis_mode=="LIVE" and self.config.telegram_signal_alerts and result.decision=="POSSIBLE_ENTRY" and result.score>=self.runtime["entry_score"]:
            telegram_key=f"SIGNAL:{self.forward_run.run_id if self.forward_run else 'NO-RUN'}:{symbol}:{result.signal_candle_time.isoformat()}"
            self._telegram_once(telegram_key,self.telegram.signal_message(
                symbol,result.score,result.setup,result.price,details.get("risk_reward"),opinion
            ),symbol,result.score)
        return analysis,result,assessment

    def _manage_positions(self, timeframe: str = "5m"):
        portfolio=ensure_portfolio(self.db,self.config.initial_balance)
        run=self.forward_run
        broker=PaperBroker(self.db,self.runtime["commission_rate"],self.runtime["slippage_rate"],self.config.intrabar_policy,
            run.run_id if run else None,run.strategy_version if run else None,run.strategy_config_hash if run else None)
        if self.config.data_mode=="live" and self.provider.name=="mock":return
        for position in list(self.db.scalars(select(Position).where(
            Position.status=="OPEN", Position.run_id==run.run_id if run else Position.run_id.is_(None)
        ))):
            try:
                candle=self.provider.get_candles(position.symbol,timeframe,2)[-1]
                trade=broker.evaluate_candle(portfolio,position,candle)
                self._log("POSITION","RE_EVALUATED",f"{timeframe} OHLC {candle.open}/{candle.high}/{candle.low}/{candle.close}",position.symbol)
                if trade is not None:
                    self._telegram_once(
                        f"SELL:{trade.id}",
                        self.telegram.sell_message(trade.symbol,trade.quantity,trade.exit_price,trade.realized_pnl,trade.exit_reason),
                        trade.symbol,trade.signal_score
                    )
            except Exception as exc:self._log("DATA_PROVIDER_ERROR","NO_TRADE",f"Pozisyon değişmedi: {safe_provider_error(exc)}",position.symbol)

    def manage_positions_only(self, timeframe: str = "5m") -> dict:
        self.forward_run=ensure_forward_run(self.db,self.config)
        before=list(self.db.scalars(select(Position.id).where(
            Position.status=="OPEN",Position.run_id==self.forward_run.run_id
        )).all())
        self._manage_positions(timeframe)
        take_snapshot(self.db,self.config.initial_balance,self.forward_run.run_id)
        upsert_daily_summary(self.db,self.config,self.forward_run)
        after=list(self.db.scalars(select(Position.id).where(
            Position.status=="OPEN",Position.run_id==self.forward_run.run_id
        )).all())
        return {"status":"positions_checked","timeframe":timeframe,"open_before":len(before),"open_after":len(after)}

    def _try_entries(self,items,funnel:dict,allow_entries:bool=True,analysis_mode:str|None=None,market_open:bool|None=None)->None:
        portfolio=ensure_portfolio(self.db,self.config.initial_balance)
        run=self.forward_run
        broker=PaperBroker(self.db,self.runtime["commission_rate"],self.runtime["slippage_rate"],self.config.intrabar_policy,
            run.run_id if run else None,run.strategy_version if run else None,run.strategy_config_hash if run else None)
        analysis_mode=analysis_mode or self.analysis_mode
        session_open=BistMarketSession.from_config(self.config).is_open()
        market_open=session_open if market_open is None else market_open and session_open
        config_matches=bool(run and run.strategy_config_hash==strategy_config_snapshot(self.analysis_config)["sha256"])
        entries_enabled=allow_entries and analysis_mode=="LIVE" and self.config.operation_mode=="LIVE_PAPER" and self.config.data_mode=="live" and self.provider.name!="mock" and market_open and bool(run and not run.paused) and config_matches
        if not entries_enabled:
            self._log("ENTRY_GATE","NO_NEW_ENTRY","Entry gate closed: provider/data/session/pause/config safety condition",
                details={"market_open":market_open,"paused":bool(run and run.paused),"provider":self.provider.name,
                    "mode":self.config.operation_mode,"analysis_mode":analysis_mode,"config_match":config_matches})
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
                position=broker.buy(portfolio,analysis.symbol,risk.quantity,analysis.price,Decimal(str(setup["invalidation_level"])),Decimal(str(setup["target"])),
                    analysis.setup,analysis.score,analysis.reason,key,result.signal_candle_time)
                funnel["buy"]+=1;self._log("ORDER","VIRTUAL_BUY",f"{risk.quantity} lot @ {analysis.price}",analysis.symbol,analysis.score)
                self._telegram_once(
                    f"BUY:{key}",
                    self.telegram.buy_message(analysis.symbol,risk.quantity,position.entry_price,position.stop_price,position.target_price,analysis.score,analysis.setup),
                    analysis.symbol,analysis.score
                )
            except DuplicateOrderError:self._log("ORDER","DUPLICATE_BLOCKED",key,analysis.symbol,analysis.score)

    def _run_cycle(self,max_symbols:int|None=None,closed_candle_timestamp:datetime|None=None)->dict:
        started=self.analysis_at or datetime.now(timezone.utc);clock=perf_counter();self.forward_run=ensure_forward_run(self.db,self.config,started)
        market_open=self.market_open if self.market_open is not None else BistMarketSession.from_config(self.config).is_open(started)
        analysis_at=started
        slot_minutes=self.config.off_hours_scan_interval_minutes if self.analysis_mode=="ANALYSIS_ONLY" else 15
        symbols=self.universe.candidates(max_symbols,started if self.analysis_mode=="ANALYSIS_ONLY" else closed_candle_timestamp,slot_minutes)
        funnel={"total":len(symbols),"data_valid":0,"sufficient_history":0,"htf_bullish":0,"valid_setup":0,"score_pass":0,"rr_pass":0,"risk_pass":0,"buy":0}
        run=ScanRun(started_at=started,provider=self.provider.name,data_mode=self.config.data_mode,total_symbols=len(symbols),valid_symbols=0,failed_symbols=0,stale_symbols=0,funnel={},errors=[],
            run_id=self.forward_run.run_id,timeframe="15m",closed_candle_timestamp=closed_candle_timestamp,
            analysis_mode=self.analysis_mode,market_open=market_open)
        self.db.add(run);self.db.commit();self._log("SCANNER","STARTED",f"{len(symbols)} sembol")
        if self.analysis_mode=="LIVE" and market_open:self._manage_positions()
        items=[];errors=[]
        for symbol in symbols:
            try:
                analysis,result,assessment=self.analyze_symbol(symbol,analysis_at);items.append((analysis,result,assessment))
                for key in ("data_valid","sufficient_history","htf_bullish","valid_setup","score_pass","rr_pass"):funnel[key]+=int(result.funnel[key])
                if not result.funnel["data_valid"]:run.stale_symbols+=1
            except Exception as exc:
                # A failed flush/commit poisons the SQLAlchemy transaction until
                # rollback. One bad symbol must not prevent the remaining scan.
                self.db.rollback()
                error=safe_provider_error(exc)
                errors.append({"symbol":symbol,"error":error});logger.warning("symbol_failed symbol=%s error=%s",symbol,error)
        self._try_entries(items,funnel,allow_entries=not errors,analysis_mode=self.analysis_mode,market_open=market_open)
        take_snapshot(self.db,self.config.initial_balance,self.forward_run.run_id)
        upsert_daily_summary(self.db,self.config,self.forward_run)
        run.completed_at=datetime.now(timezone.utc);run.duration_ms=round((perf_counter()-clock)*1000);run.valid_symbols=len(items);run.failed_symbols=len(errors);run.funnel=funnel;run.errors=errors
        run.watchlist_count=self.db.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.run_id==self.forward_run.run_id)) or 0
        run.source_candle_timestamp=max((item[1].signal_candle_time for item in items),default=None)
        if self.analysis_mode=="ANALYSIS_ONLY":run.closed_candle_timestamp=run.source_candle_timestamp
        run.signals=sum(item[0].decision=="POSSIBLE_ENTRY" for item in items);run.entries=funnel["buy"];self.db.commit()
        self._log("SCANNER","COMPLETED",f"{len(items)} analiz, {len(errors)} hata, {funnel['buy']} BUY",details=funnel)
        return {"status":"completed","analyzed":len(items),"errors":errors,"funnel":funnel,"duration_ms":run.duration_ms,
            "analysis_mode":self.analysis_mode,"market_open":market_open,"entries_enabled":self.analysis_mode=="LIVE" and market_open,
            "source_candle_timestamp":run.source_candle_timestamp,
            "results":[{"symbol":a.symbol,"score":a.score,"decision":a.decision,"source":a.data_source,
                "universe_status":assessment.status} for a,_,assessment in sorted(items,key=lambda pair:pair[0].score,reverse=True)]}

    def run(self,max_symbols:int|None=None,closed_candle_timestamp:datetime|None=None)->dict:
        if self.config.operation_mode!="LIVE_PAPER":return {"status":"wrong_mode","analyzed":0,"errors":[],"results":[]}
        if not _SCAN_LOCK.acquire(blocking=False):return {"status":"already_running","analyzed":0,"errors":[],"results":[]}
        try:return self._run_cycle(max_symbols,closed_candle_timestamp)
        finally:_SCAN_LOCK.release()
