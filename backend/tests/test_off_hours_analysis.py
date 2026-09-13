from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine,func,select
from sqlalchemy.orm import Session

from app.api.routes import scanner_run,scanner_status
from app.analysis.pipeline import PipelineResult
from app.config.settings import AppSettings
from app.db.session import Base
from app.market_data.market_session import BistMarketSession
from app.market_data.provider import CandleData
from app.models import Analysis,MarketStateSnapshot,Order,Position,ScanRun,Trade,WatchlistItem
from app.news.models import NewsRecord
from app.news.service import NewsService
from app.scanner.bist_scanner import BistScanner
from app.scanner.universe_builder import UniverseBuilder
from app.scanner.watchlist_manager import update_watchlist
from app.services.forward_test import ensure_forward_run
from app.services.forward_worker import ForwardWorker
import app.news.service as news_module


SUNDAY=datetime(2026,9,13,9,tzinfo=timezone.utc)
FRIDAY={"15m":datetime(2026,9,11,14,45,tzinfo=timezone.utc),"1h":datetime(2026,9,11,14,tzinfo=timezone.utc),
    "1d":datetime(2026,9,11,12,tzinfo=timezone.utc)}

def database():
    engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine);return engine

def config(**updates):
    values={"data_mode":"live","market_data_provider":"yahoo","operation_mode":"LIVE_PAPER","ai_enabled":False,
        "telegram_enabled":False,"market_memory_enabled":True,"off_hours_scan_enabled":True,
        "off_hours_scan_interval_minutes":30,"off_hours_scan_symbol_limit":1}
    values.update(updates);return AppSettings(_env_file=None,**values)

class FridayProvider:
    name="yahoo"
    def get_symbols(self):return ["TEST"]
    def get_candles(self,symbol,timeframe,limit=220):
        step={"15m":timedelta(minutes=15),"1h":timedelta(hours=1),"1d":timedelta(days=1)}[timeframe]
        end=FRIDAY[timeframe];rows=[]
        for index in range(limit):
            value=Decimal("50")+Decimal(index)/Decimal("10")
            rows.append(CandleData(end-step*(limit-index-1),value,value+1,value-1,value+Decimal("0.5"),Decimal("100000"),True))
        return rows

def run_closed(db,at=SUNDAY):
    with patch("app.services.forward_worker.fetch_xu100_price",return_value=(Decimal("100"),at)):
        return ForwardWorker(db,config(),FridayProvider()).run_once(at,max_symbols=1)

def test_market_closed_scanner_creates_analysis_but_no_trading_rows():
    with Session(database(),expire_on_commit=False) as db:
        result=run_closed(db)
        assert result["status"]=="completed" and result["analyzed"]==1
        analysis=db.scalar(select(Analysis));run=db.scalar(select(ScanRun))
        assert analysis is not None and analysis.details["analysis_mode"]=="ANALYSIS_ONLY"
        assert analysis.details["market_open"] is False and run.analysis_mode=="ANALYSIS_ONLY" and run.entries==0
        assert db.scalar(select(Order)) is None and db.scalar(select(Position)) is None and db.scalar(select(Trade)) is None

def test_friday_closing_candles_are_fresh_for_weekend_analysis():
    session=BistMarketSession.from_config(config())
    assert all(session.freshness(FRIDAY[tf],tf,SUNDAY,90)=="FRESH" for tf in FRIDAY)
    with Session(database()) as db:
        run_closed(db);row=db.scalar(select(Analysis))
        assert row.data_valid is True and set(row.details["analysis_context"]["freshness"].values())=={"FRESH"}

def test_old_session_candle_is_stale_even_while_market_closed():
    session=BistMarketSession.from_config(config())
    assert session.freshness(FRIDAY["15m"]-timedelta(days=7),"15m",SUNDAY,90)=="STALE"

def test_off_hours_cadence_waits_then_runs_again():
    with Session(database(),expire_on_commit=False) as db:
        first=run_closed(db);waiting=run_closed(db,SUNDAY+timedelta(minutes=10));second=run_closed(db,SUNDAY+timedelta(minutes=31))
        assert first["status"]=="completed" and waiting["status"]=="off_hours_waiting" and second["status"]=="completed"
        assert db.scalar(select(func.count()).select_from(ScanRun))==2

def test_off_hours_snapshot_is_tagged_and_deduplicated_by_source_candle():
    with Session(database()) as db:
        run_closed(db);run_closed(db,SUNDAY+timedelta(minutes=31))
        rows=db.scalars(select(MarketStateSnapshot)).all()
        assert len(rows)==1 and rows[0].analysis_mode=="ANALYSIS_ONLY"
        assert rows[0].timestamp.replace(tzinfo=timezone.utc)==FRIDAY["15m"]

def test_off_hours_rotation_uses_configured_slots():
    symbols=[f"S{x}" for x in range(7)];builder=UniverseBuilder(SimpleNamespace(get_symbols=lambda:symbols),config())
    batches=[builder.candidates(3,SUNDAY+timedelta(minutes=30*x),30) for x in range(3)]
    assert len(set(batches[0])&set(batches[1]))==0 and set().union(*map(set,batches))==set(symbols)

def test_high_score_analysis_only_hard_gate_creates_no_order():
    with Session(database()) as db:
        cfg=config();scanner=BistScanner(db,cfg,FridayProvider(),require_market_session=False,analysis_mode="ANALYSIS_ONLY",market_open=False)
        scanner.forward_run=ensure_forward_run(db,cfg,SUNDAY)
        analysis=SimpleNamespace(decision="POSSIBLE_ENTRY",symbol="TEST",setup="BREAKOUT",score=100,price=Decimal("100"),reason="test",
            details={"setup":{"entry_area":100,"invalidation_level":95,"target":110},"volatility":{"atr":2}})
        result=SimpleNamespace(signal_candle_time=FRIDAY["15m"]);assessment=SimpleNamespace(affordable=True,reason="ok")
        with patch("app.scanner.bist_scanner.BistMarketSession.is_open",return_value=False):
            scanner._try_entries([(analysis,result,assessment)],{"risk_pass":0,"buy":0},analysis_mode="ANALYSIS_ONLY",market_open=False)
        assert db.scalar(select(Order)) is None

def test_watchlist_uses_off_hours_status_without_entry_impression():
    with Session(database()) as db:
        item=update_watchlist(db,"TEST",90,"BREAKOUT","candidate",70,82,run_id="run",analysis_mode="ANALYSIS_ONLY")
        assert item.status=="POSSIBLE_ENTRY_ANALYSIS_ONLY"

def test_manual_closed_market_scan_returns_explicit_analysis_only_metadata():
    cfg=config();response={"status":"completed","analysis_mode":"ANALYSIS_ONLY","market_open":False,"entries_enabled":False,"analyzed":10,"errors":[],"results":[]}
    with Session(database()) as db,patch("app.api.routes.config",cfg),patch("app.api.routes.BistMarketSession.is_open",return_value=False),patch("app.api.routes.BistScanner") as scanner:
        scanner.return_value.run.return_value=response
        result=scanner_run(10,db)
        assert result==response and scanner.call_args.kwargs["analysis_mode"]=="ANALYSIS_ONLY"

def test_scanner_status_exposes_closed_market_controls_and_last_candle():
    cfg=config()
    with Session(database()) as db,patch("app.api.routes.config",cfg),patch("app.api.routes.BistMarketSession.is_open",return_value=False):
        run_closed(db);status=scanner_status(db)
        assert status["analysis_mode"]=="ANALYSIS_ONLY" and status["entries_enabled"] is False
        assert status["last_market_candle"] and status["off_hours_scan_interval_minutes"]==30 and status["off_hours_scan_symbol_limit"]==1

def _high_result():
    details={"analysis_context":{"daily_candle_time":FRIDAY["1d"],"hourly_candle_time":FRIDAY["1h"],"entry_candle_time":FRIDAY["15m"],"freshness":{"1d":"FRESH","1h":"FRESH","15m":"FRESH"}},
        "analysis_complete":True,"timeframes":{"1d":{"label":"bullish"}},"structure":{"label":"BULLISH"},"momentum":{"label":"strong"},
        "volume":{"rvol":2},"volatility":{"atr":Decimal("2")},"indicators":{},"levels":{"support":49,"resistance":60},
        "setup":{"entry_area":55,"invalidation_level":50,"target":70,"score":100},"risk_reward":Decimal("3")}
    funnel={key:True for key in ("data_valid","sufficient_history","htf_bullish","valid_setup","score_pass","rr_pass","session_valid","source_allowed")}
    return PipelineResult("TEST",Decimal("55"),100,"bullish","BULLISH","BREAKOUT","POSSIBLE_ENTRY","strong",details,FRIDAY["15m"],funnel)

def test_telegram_signal_is_never_sent_from_analysis_only_scan():
    cfg=config(telegram_enabled=True,telegram_signal_alerts=True,telegram_off_hours_analysis=False,market_memory_enabled=False)
    with Session(database()) as db:
        scanner=BistScanner(db,cfg,FridayProvider(),require_market_session=False,analysis_mode="ANALYSIS_ONLY",market_open=False,analysis_at=SUNDAY)
        scanner.forward_run=ensure_forward_run(db,cfg,SUNDAY)
        with patch("app.scanner.bist_scanner.analyze_frames",return_value=_high_result()),patch.object(scanner,"_telegram_once") as notify:
            scanner.analyze_symbol("TEST",SUNDAY)
        notify.assert_not_called()

def test_news_telegram_path_is_unaffected_by_off_hours_signal_setting():
    class Source:
        name="AA"
        def fetch(self):return [NewsRecord("AA","news-1","ASELSAN sözleşme","", "https://example.com/news",SUNDAY)]
    class Notifier:
        calls=0
        def send(self,_):self.calls+=1;return True
    notifier=Notifier();news_module._LAST_REFRESH_AT=0
    with Session(database()) as db:
        NewsService(db,config(news_enabled=True),[Source()],notifier=notifier).refresh()
        assert notifier.calls==1
