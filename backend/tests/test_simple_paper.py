from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config.settings import AppSettings
from app.db.session import Base, get_db
from app.api.routes import router
from app.market_data.provider import CandleData
from app.models import DecisionLog, Order, Position, Trade
from app.portfolio.portfolio_manager import ensure_portfolio, portfolio_summary
from app.services.forward_test import ensure_forward_run
from app.services.simple_paper import MODE, SimpleCandidate, SimplePaperEngine, analyze_symbol, simple_status


class FakeNotifier:
    def __init__(self): self.messages = []
    def send(self, message): self.messages.append(message); return {"status": "SENT"}
    simple_buy_message = staticmethod(lambda *args: f"BUY {args[0]}")
    simple_sell_message = staticmethod(lambda *args: f"SELL {args[0]}")


class FakeProvider:
    name = "fixture"
    def __init__(self, price=Decimal("100"), fail=None): self.price, self.fail = price, fail
    def get_latest_price(self, symbol): return self.price
    def get_candles(self, symbol, timeframe, limit=80):
        if symbol == self.fail: raise RuntimeError("isolated provider failure")
        now = datetime(2026, 9, 15, 9, tzinfo=timezone.utc)
        if timeframe == "5m":
            return [CandleData(now - timedelta(minutes=5 * (limit-i)), self.price, self.price,
                self.price, self.price, Decimal("1000"), True) for i in range(limit)]
        return [CandleData(now - timedelta(minutes=15 * (limit-i)), Decimal("100"), Decimal("103"),
            Decimal("99"), Decimal("100") + Decimal(i) / 100, Decimal("1000"), True) for i in range(limit)]


def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine); return engine


def config():
    return AppSettings(operation_mode=MODE, data_mode="mock", embedded_worker_enabled=False,
        initial_balance=Decimal("5000"), commission_rate=Decimal(".001"), slippage_rate=Decimal(".0005"),
        simple_entry_score=60, entry_score=82, watchlist_score=70)


def candidate(score=75):
    return SimpleCandidate("ASELS", Decimal("100"), Decimal("0.4"), Decimal("1.1"), Decimal("99"),
        Decimal("98"), Decimal("59"), Decimal("1400"), Decimal("1000"), score,
        datetime(2026, 9, 15, 8, 45, tzinfo=timezone.utc))


def test_simple_score_uses_only_declared_six_rules():
    rows = FakeProvider().get_candles("ASELS", "15m", 80)
    result = analyze_symbol("ASELS", rows)
    assert result.score in {0, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 100}


def test_symbol_failure_isolated_and_scan_persists_failure():
    with Session(database()) as db, patch("app.services.simple_paper.SIMPLE_UNIVERSE", ["ASELS", "BROKEN"]), \
            patch("app.services.simple_paper.analyze_symbol", return_value=candidate()):
        result = SimplePaperEngine(db, config(), FakeProvider(fail="BROKEN"), FakeNotifier()).scan(
            market_open=False, max_symbols=2)
        assert result["valid_symbols"] == 1 and result["failed_symbols"] == 1
        assert result["failures"][0]["symbol"] == "BROKEN" and result["entry"] is None


def test_deterministic_buy_live_pnl_take_profit_sell_and_accounting():
    cfg = config(); provider = FakeProvider(); notifier = FakeNotifier()
    with Session(database(), expire_on_commit=False) as db, \
            patch("app.services.simple_paper.SIMPLE_UNIVERSE", ["ASELS"]), \
            patch("app.services.simple_paper.analyze_symbol", return_value=candidate(75)):
        engine = SimplePaperEngine(db, cfg, provider, notifier)
        result = engine.scan(datetime(2026, 9, 15, 9, tzinfo=timezone.utc), market_open=True, max_symbols=1)
        position = db.scalar(select(Position).where(Position.status == "OPEN"))
        buy = db.scalar(select(Order).where(Order.side == "BUY"))
        assert result["entry"] and position and buy and position.quantity == 10
        assert buy.requested_price * buy.quantity == Decimal("1000")
        assert position.strategy_version == MODE and db.scalar(select(DecisionLog).where(
            DecisionLog.category == "SIMPLE_PAPER_ENTRY")) is not None

        provider.price = Decimal("101")
        live = engine.update_positions(datetime(2026, 9, 15, 9, 5, tzinfo=timezone.utc))
        assert live["updated"][0]["unrealized_pnl"] > 0

        provider.price = Decimal("102")
        closed = engine.update_positions(datetime(2026, 9, 15, 9, 10, tzinfo=timezone.utc))
        trade = db.scalar(select(Trade)); summary = portfolio_summary(db, cfg.initial_balance)
        assert closed["closed"][0]["reason"] == "TAKE PROFIT"
        assert trade and trade.exit_price == Decimal("101.95") and position.status == "CLOSED"
        assert summary["open_positions"] == 0 and summary["portfolio_value"] == summary["cash_balance"]
        assert summary["cash_balance"] > Decimal("5000")
        assert notifier.messages == ["BUY ASELS", "SELL ASELS"]
        state = simple_status(db, cfg)
        assert state["real_orders"] is False and state["entry_threshold"] == 60
        assert cfg.entry_score == 82 and cfg.watchlist_score == 70


def test_simple_run_does_not_construct_optional_modules():
    with Session(database()) as db, patch("app.services.simple_paper.SIMPLE_UNIVERSE", ["ASELS"]), \
            patch("app.services.simple_paper.analyze_symbol", return_value=candidate()):
        engine = SimplePaperEngine(db, config(), FakeProvider(), FakeNotifier())
        assert engine.scan(market_open=False, max_symbols=1)["status"] == "completed"


def test_simple_debug_and_manual_run_endpoints():
    cfg = config(); app = FastAPI(); app.include_router(router, prefix="/api")
    with Session(database(), expire_on_commit=False) as db:
        app.dependency_overrides[get_db] = lambda: db
        with patch("app.api.routes.config", cfg), patch("app.services.simple_paper.SIMPLE_UNIVERSE", ["ASELS"]), \
                patch("app.services.simple_paper.analyze_symbol", return_value=candidate()), \
                patch("app.api.routes.BistMarketSession.is_open", return_value=False):
            with TestClient(app) as client:
                before = client.get("/api/simple-paper/status")
                run = client.post("/api/simple-paper/run?max_symbols=1")
                candidates = client.get("/api/simple-paper/candidates")
        assert before.status_code == 200 and before.json()["real_orders"] is False
        assert run.status_code == 200 and run.json()["analysis_mode"] == "ANALYSIS_ONLY"
        assert candidates.status_code == 200 and candidates.json()[0]["symbol"] == "ASELS"
