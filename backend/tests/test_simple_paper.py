from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import router
from app.config.settings import AppSettings
from app.db.session import Base, get_db
from app.market_data.provider import CandleData
from app.models import DecisionLog
from app.services.simple_paper import MODE, SimpleCandidate, SimplePaperEngine, bootstrap_simple_state, simple_status
from app.services.simple_state import clear_simple_state_cache, get_simple_state
from app.services.telegram import TelegramNotifier
from app.services.telegram_commands import TelegramCommandService


NOW = datetime(2026, 9, 15, 9, tzinfo=timezone.utc)


class FakeNotifier:
    simple_buy_message = staticmethod(TelegramNotifier.simple_buy_message)
    simple_sell_message = staticmethod(TelegramNotifier.simple_sell_message)
    simple_best_message = staticmethod(TelegramNotifier.simple_best_message)
    def __init__(self, fail=False): self.messages, self.fail = [], fail
    def send(self, message):
        if self.fail: raise RuntimeError("telegram unavailable")
        self.messages.append(message); return {"status": "SENT"}


class FakeProvider:
    name = "fixture"
    def __init__(self, price=Decimal("100"), fail=None): self.price, self.fail = price, fail
    def get_candles(self, symbol, timeframe, limit=80):
        if symbol == self.fail: raise RuntimeError("isolated provider failure")
        if timeframe == "5m":
            return [CandleData(NOW-timedelta(minutes=5*(limit-i)), self.price, self.price,
                self.price, self.price, Decimal("1000"), True) for i in range(limit)]
        return [CandleData(NOW-timedelta(minutes=15*(limit-i)), Decimal("100"), Decimal("103"),
            Decimal("99"), Decimal("100")+Decimal(i)/100, Decimal("1000"), True) for i in range(limit)]


class BrokenDb:
    def execute(self, *_): raise ConnectionError("postgres unavailable secret-url-must-not-log")
    def scalar(self, *_): raise ConnectionError("postgres unavailable")
    def add(self, *_): raise ConnectionError("postgres unavailable")
    def commit(self): raise ConnectionError("postgres unavailable")
    def rollback(self): pass
    def close(self): pass


def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine); return engine


def config(tmp_path, **changes):
    clear_simple_state_cache()
    return AppSettings(operation_mode=MODE, data_mode="mock", embedded_worker_enabled=False,
        initial_balance=Decimal("5000"), commission_rate=Decimal(".001"), slippage_rate=Decimal(".0005"),
        simple_entry_score=60, entry_score=82, watchlist_score=70,
        simple_state_path=str(tmp_path/"simple-state.json"), **changes)


def candidate(score=75, candle_time=NOW-timedelta(minutes=15), symbol="ASELS"):
    return SimpleCandidate(symbol, Decimal("100"), Decimal("0.4"), Decimal("1.1"), Decimal("99"),
        Decimal("98"), Decimal("59"), Decimal("1400"), Decimal("1000"), score, candle_time)


def test_db_failure_full_buy_live_pnl_sell_and_telegram(tmp_path):
    cfg=config(tmp_path); provider=FakeProvider(); notifier=FakeNotifier()
    with patch("app.services.simple_paper.SIMPLE_UNIVERSE", ["ASELS"]), \
            patch("app.services.simple_paper.analyze_symbol", return_value=candidate()):
        engine=SimplePaperEngine(BrokenDb(),cfg,provider,notifier)
        result=engine.scan(NOW,market_open=True,max_symbols=1)
        assert result["entry"]["quantity"]==10 and get_simple_state(cfg).read()["open_position"]["symbol"]=="ASELS"
        provider.price=Decimal("101")
        assert engine.update_positions(NOW+timedelta(minutes=5))["updated"][0]["unrealized_pnl"]>0
        provider.price=Decimal("102")
        closed=engine.update_positions(NOW+timedelta(minutes=10))["closed"][0]
    state=get_simple_state(cfg).read(NOW+timedelta(minutes=10))
    assert closed["reason"]=="TAKE PROFIT" and state["open_position"] is None and state["cash"]>5000
    assert state["database_status"]=="DEGRADED" and state["state_backend"]=="LOCAL_JSON"
    assert any("PAPER BUY" in item for item in notifier.messages) and any("PAPER SELL" in item for item in notifier.messages)


def test_restart_restores_position_and_prevents_duplicate_buy(tmp_path):
    cfg=config(tmp_path); store=get_simple_state(cfg); engine=SimplePaperEngine(None,cfg,FakeProvider(),FakeNotifier(),store)
    with patch("app.services.simple_paper.SIMPLE_UNIVERSE", ["ASELS"]), \
            patch("app.services.simple_paper.analyze_symbol", return_value=candidate()):
        assert engine.scan(NOW,market_open=True,max_symbols=1)["entry"]
        clear_simple_state_cache(); restored=get_simple_state(cfg)
        assert restored.read()["open_position"]["symbol"]=="ASELS"
        second=SimplePaperEngine(None,cfg,FakeProvider(),FakeNotifier(),restored).scan(NOW+timedelta(minutes=15),market_open=True,max_symbols=1)
        assert second["entry"] is None and second["entry_block_reason"]=="OPEN_POSITION_EXISTS"


def test_stale_data_blocks_once(tmp_path):
    cfg=config(tmp_path); notifier=FakeNotifier(); stale=candidate(candle_time=NOW-timedelta(minutes=31))
    with patch("app.services.simple_paper.SIMPLE_UNIVERSE", ["ASELS"]), patch("app.services.simple_paper.analyze_symbol",return_value=stale):
        engine=SimplePaperEngine(None,cfg,FakeProvider(),notifier)
        first=engine.scan(NOW,market_open=True,max_symbols=1); engine.scan(NOW+timedelta(minutes=1),market_open=True,max_symbols=1)
    assert first["entry_block_reason"]=="STALE_DATA_BLOCK" and get_simple_state(cfg).read()["open_position"] is None
    assert sum("STALE DATA BLOCK" in item for item in notifier.messages)==1


def test_cooldown_trade_limit_and_daily_loss_brake(tmp_path):
    cfg=config(tmp_path); store=get_simple_state(cfg); engine=SimplePaperEngine(None,cfg,FakeProvider(),FakeNotifier(),store)
    payload=candidate().payload(60,"test")
    store.mutate(lambda state: state["last_exit_by_symbol"].update(ASELS=(NOW-timedelta(minutes=30)).isoformat()),NOW)
    assert engine._entry_gate(payload,NOW,True,True)==(False,"COOLDOWN")
    store.mutate(lambda state:(state["last_exit_by_symbol"].clear(),state.update(trades_today=5)),NOW)
    assert engine._entry_gate(payload,NOW,True,True)==(False,"DAILY_TRADE_LIMIT")
    store.mutate(lambda state:state.update(trades_today=0,daily_realized_pnl=-100),NOW)
    assert engine._entry_gate(payload,NOW,True,True)==(False,"DAILY_LOSS_BRAKE")


def test_daily_summary_is_sent_once_without_database(tmp_path):
    cfg=config(tmp_path); notifier=FakeNotifier(); engine=SimplePaperEngine(None,cfg,FakeProvider(),notifier)
    first=engine.daily_summary(NOW); second=engine.daily_summary(NOW+timedelta(minutes=1))
    assert first["status"]=="SENT" and second["status"]=="ALREADY_SENT"
    assert sum("GÜNLÜK PAPER ÖZET" in item for item in notifier.messages)==1


def test_best_and_candidate_commands_use_cached_state_without_db(tmp_path):
    cfg=config(tmp_path); best=candidate().payload(60,"test")
    get_simple_state(cfg).mutate(lambda state:state.update(last_scan={"best_candidate":best,"candidates":[best],
        "valid_symbols":1,"failed_symbols":0,"scanned_at":NOW.isoformat()}),NOW)
    service=TelegramCommandService(BrokenDb(),cfg)
    assert "EN İYİ ADAY" in service.dispatch("best") and service.dispatch("candidate")==service.dispatch("best")
    response=service.handle_update({"update_id":1,"message":{"text":"/best","chat":{"id":str(cfg.telegram_chat_id)}}})
    assert response and "ASELS" in response


def test_database_available_is_best_effort_audit_backend(tmp_path):
    cfg=config(tmp_path)
    with Session(database()) as db, patch("app.services.simple_paper.SIMPLE_UNIVERSE", ["ASELS"]), \
            patch("app.services.simple_paper.analyze_symbol", return_value=candidate()):
        engine=SimplePaperEngine(db,cfg,FakeProvider(),FakeNotifier()); engine.scan(NOW,market_open=False,max_symbols=1)
        assert db.scalar(select(DecisionLog).where(DecisionLog.category=="SIMPLE_STATE"))
        assert get_simple_state(cfg).read()["state_backend"]=="DATABASE"


def test_corrupt_json_recovers_flat_and_cold_start_alert_once(tmp_path):
    cfg=config(tmp_path); path=tmp_path/"simple-state.json"; path.write_text("{broken",encoding="utf-8")
    clear_simple_state_cache(); notifier=FakeNotifier(); store=bootstrap_simple_state(None,cfg,notifier)
    bootstrap_simple_state(None,cfg,notifier)
    assert store.read()["cash"]==5000 and store.corrupt_recovered
    assert sum("SIMPLE STATE RESTARTED" in item for item in notifier.messages)==1


def test_health_and_simple_endpoints_survive_database_failure(tmp_path):
    cfg=config(tmp_path); app=FastAPI(); app.include_router(router,prefix="/api"); app.dependency_overrides[get_db]=lambda:BrokenDb()
    with patch("app.api.routes.config",cfg), patch("app.services.simple_paper.SIMPLE_UNIVERSE",["ASELS"]), \
            patch("app.services.simple_paper.analyze_symbol",return_value=candidate()), \
            patch("app.api.routes.BistMarketSession.is_open",return_value=False):
        with TestClient(app) as client:
            health=client.get("/api/health"); run=client.post("/api/simple-paper/run?max_symbols=1")
            status=client.get("/api/simple-paper/status"); candidates=client.get("/api/simple-paper/candidates")
    assert health.status_code==200 and health.json()["database"]=="DEGRADED" and health.json()["simple_paper"]=="RUNNING"
    assert run.status_code==200 and run.json()["valid_symbols"]==1
    assert status.json()["database_required"] is False and candidates.json()[0]["symbol"]=="ASELS"


def test_telegram_failure_does_not_block_trade(tmp_path):
    cfg=config(tmp_path)
    with patch("app.services.simple_paper.SIMPLE_UNIVERSE",["ASELS"]),patch("app.services.simple_paper.analyze_symbol",return_value=candidate()):
        result=SimplePaperEngine(None,cfg,FakeProvider(),FakeNotifier(fail=True)).scan(NOW,market_open=True,max_symbols=1)
    assert result["entry"] and get_simple_state(cfg).read()["open_position"]
    assert cfg.entry_score==82 and cfg.watchlist_score==70 and cfg.simple_entry_score==60


def test_cooldown_symbol_skips_to_another_candidate(tmp_path):
    cfg=config(tmp_path); store=get_simple_state(cfg)
    store.mutate(lambda state:state["last_exit_by_symbol"].update(ASELS=(NOW-timedelta(minutes=10)).isoformat()),NOW)
    candidates=[candidate(75,symbol="ASELS"),candidate(70,symbol="ANHYT")]
    with patch("app.services.simple_paper.SIMPLE_UNIVERSE",["ASELS","ANHYT"]), \
            patch("app.services.simple_paper.analyze_symbol",side_effect=candidates):
        result=SimplePaperEngine(None,cfg,FakeProvider(),FakeNotifier(),store).scan(NOW,market_open=True,max_symbols=2)
    assert result["entry"]["symbol"]=="ANHYT"


def test_production_lifespan_survives_database_init_failure(tmp_path):
    from app.main import app as production_app, settings as production_settings
    production_app.dependency_overrides[get_db]=lambda:BrokenDb()
    with patch.object(production_settings,"operation_mode",MODE), \
            patch.object(production_settings,"simple_state_path",str(tmp_path/"startup.json")), \
            patch.object(production_settings,"embedded_worker_enabled",False), \
            patch.object(production_settings,"telegram_enabled",False), \
            patch("app.main.Base.metadata.create_all",side_effect=ConnectionError("postgres unavailable")):
        clear_simple_state_cache()
        with TestClient(production_app) as client:
            response=client.get("/api/health")
    production_app.dependency_overrides.pop(get_db,None)
    assert response.status_code==200 and response.json()["database"]=="DEGRADED"
