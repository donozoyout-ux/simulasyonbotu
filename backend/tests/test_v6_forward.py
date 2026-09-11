from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session

from app.config.settings import AppSettings
from app.db.session import Base
from app.market_data.provider import CandleData
from app.models import ForwardRun,Order,PortfolioSnapshot,ProcessedCandle,Trade
from app.portfolio.paper_broker import DuplicateOrderError,PaperBroker
from app.portfolio.portfolio_manager import ensure_portfolio,take_snapshot
from app.scanner.bist_scanner import BistScanner
from app.services.forward_test import ensure_forward_run,reset_forward_run,set_paused,upsert_daily_summary
from app.services.forward_worker import ForwardWorker,expected_closed_candle


def database():
    engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine);return engine


def config(**changes):
    return AppSettings(auto_scan_enabled=False,operation_mode="LIVE_PAPER",data_mode="live",**changes)


def provider(name="yahoo"):
    return SimpleNamespace(name=name,get_symbols=lambda:[],get_latest_price=lambda _symbol:Decimal("100"))


def test_forward_run_persists_across_restart_and_provider_switch_keeps_strategy():
    engine=database();at=datetime(2026,9,11,7,tzinfo=timezone.utc)
    with Session(engine,expire_on_commit=False) as db:
        first=ensure_forward_run(db,config(),at);first_id=first.run_id;started=first.started_at;strategy=first.strategy_config_hash
    with Session(engine,expire_on_commit=False) as db:
        second=ensure_forward_run(db,config(market_data_provider="twelvedata"),at)
        assert second.run_id==first_id and second.started_at==started
        assert second.strategy_config_hash==strategy and second.provider=="twelvedata"


def test_same_closed_candle_is_processed_once():
    engine=database();cfg=config();at=datetime(2026,9,11,7,16,tzinfo=timezone.utc)
    with Session(engine,expire_on_commit=False) as db,patch("app.services.forward_worker.BistScanner.run",return_value={"status":"completed"}) as scan:
        worker=ForwardWorker(db,cfg,provider());first=worker.run_once(at);second=worker.run_once(at)
        assert first["status"]=="completed" and second["status"]=="already_processed"
        assert scan.call_count==1 and db.scalar(select(ProcessedCandle)).status=="COMPLETE"


def test_worker_failed_marker_is_retried_after_crash():
    engine=database();cfg=config();at=datetime(2026,9,11,7,16,tzinfo=timezone.utc)
    with Session(engine,expire_on_commit=False) as db:
        worker=ForwardWorker(db,cfg,provider())
        with patch("app.services.forward_worker.BistScanner.run",side_effect=RuntimeError("worker crash")),pytest.raises(RuntimeError):
            worker.run_once(at)
        assert db.scalar(select(ProcessedCandle)).status=="FAILED"
        with patch("app.services.forward_worker.BistScanner.run",return_value={"status":"completed"}):
            assert worker.run_once(at)["status"]=="completed"


def test_weekend_has_no_scan():
    cfg=config();saturday=datetime(2026,9,12,9,tzinfo=timezone.utc)
    assert expected_closed_candle(cfg,saturday) is None
    with Session(database()) as db:
        assert ForwardWorker(db,cfg,provider()).run_once(saturday)["status"]=="market_closed"


def _possible_entry():
    analysis=SimpleNamespace(decision="POSSIBLE_ENTRY",symbol="TEST",setup="BREAKOUT",score=90,price=Decimal("100"),
        reason="test",details={"setup":{"entry_area":100,"invalidation_level":95,"target":110},"volatility":{"atr":2}})
    result=SimpleNamespace(signal_candle_time=datetime(2026,9,11,7,tzinfo=timezone.utc))
    assessment=SimpleNamespace(affordable=True,reason="ok")
    return analysis,result,assessment


@pytest.mark.parametrize("blocked_by",["paused","mock","market_closed"])
def test_live_entry_safety_gates(blocked_by):
    engine=database();cfg=config();source=provider("mock" if blocked_by=="mock" else "yahoo")
    with Session(engine,expire_on_commit=False) as db:
        scanner=BistScanner(db,cfg,source);scanner.forward_run=ensure_forward_run(db,cfg)
        if blocked_by=="paused":scanner.forward_run=set_paused(db,cfg,True)
        market_open=blocked_by!="market_closed"
        with patch("app.scanner.bist_scanner.BistMarketSession.is_open",return_value=market_open):
            scanner._try_entries([_possible_entry()],{"risk_pass":0,"buy":0})
        assert db.scalar(select(Order)) is None


def test_duplicate_live_buy_is_idempotent_and_run_linked():
    engine=database();cfg=config()
    with Session(engine,expire_on_commit=False) as db:
        run=ensure_forward_run(db,cfg);portfolio=ensure_portfolio(db,cfg.initial_balance)
        broker=PaperBroker(db,cfg.commission_rate,cfg.slippage_rate,run_id=run.run_id,
            strategy_version=run.strategy_version,strategy_config_hash=run.strategy_config_hash)
        broker.buy(portfolio,"TEST",5,Decimal("100"),Decimal("95"),Decimal("110"),"BREAKOUT",90,"test","same")
        with pytest.raises(DuplicateOrderError):broker.buy(portfolio,"TEST",5,Decimal("100"),Decimal("95"),Decimal("110"),"BREAKOUT",90,"test","same")
        assert db.scalar(select(Order)).run_id==run.run_id


def test_reset_archives_old_run_and_preserves_trade_history():
    engine=database();cfg=config();at=datetime(2026,9,11,7,tzinfo=timezone.utc)
    with Session(engine,expire_on_commit=False) as db:
        run=ensure_forward_run(db,cfg,at);portfolio=ensure_portfolio(db,cfg.initial_balance)
        broker=PaperBroker(db,cfg.commission_rate,cfg.slippage_rate,run_id=run.run_id,strategy_version=run.strategy_version)
        position=broker.buy(portfolio,"TEST",5,Decimal("100"),Decimal("95"),Decimal("110"),"BREAKOUT",90,"test","old")
        broker.sell(portfolio,position,5,Decimal("105"),"target")
        new=reset_forward_run(db,cfg,datetime(2026,9,11,8,tzinfo=timezone.utc))
        assert new.run_id!=run.run_id and db.get(ForwardRun,run.run_id).status=="ARCHIVED"
        assert len(db.scalars(select(Trade).where(Trade.run_id==run.run_id)).all())==1
        assert ensure_portfolio(db,cfg.initial_balance).cash_balance==cfg.initial_balance


def test_daily_summary_and_equity_snapshot_are_run_isolated():
    engine=database();cfg=config();at=datetime(2026,9,11,7,tzinfo=timezone.utc)
    with Session(engine,expire_on_commit=False) as db:
        run=ensure_forward_run(db,cfg,at);take_snapshot(db,cfg.initial_balance,run.run_id)
        summary=upsert_daily_summary(db,cfg,run,at)
        assert summary.run_id==run.run_id and summary.ending_equity==Decimal("5000")
        assert len(db.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.run_id==run.run_id)).all())>=1
