from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from types import SimpleNamespace
from unittest.mock import patch

from app.analysis.indicators import atr, ema, macd
from app.analysis.pipeline import analyze_frames
from app.analysis.support_resistance import nearest_zones
from app.config.settings import AppSettings
from app.db.session import Base
from app.market_data.market_session import BistMarketSession
from app.market_data.provider import CandleData
from app.models import Order, Portfolio, Trade
from app.portfolio.paper_broker import DuplicateOrderError, PaperBroker
from app.portfolio.risk_manager import size_position
from app.replay.engine import ReplayEngine
from app.strategy.breakout import detect_breakout
from app.strategy.signal_engine import decide


def candle(at, open_=100, high=102, low=98, close=101, volume=1000, closed=True):
    return CandleData(at, *(Decimal(str(v)) for v in (open_, high, low, close, volume)), closed)


def test_open_candle_cannot_trigger_breakout():
    at = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    candles = [candle(at, 99, 100, 98, 99), candle(at + timedelta(minutes=15), 99, 104, 99, 103, 2000, False)]
    result = detect_breakout(candles, {"high": Decimal("100")}, {"confirmed": True, "rvol": Decimal("2")},
                             {"body_pct": Decimal("0.8"), "upper_wick_pct": Decimal("0.1"), "quality_score": 90})
    assert not result["detected"]
    assert "closed_above" in result["reason"]


def test_market_session_closure_and_weekend_freshness():
    session = BistMarketSession()
    friday_close_bar = datetime(2026, 9, 4, 14, 45, tzinfo=timezone.utc)  # 17:45 Istanbul
    monday_open = datetime(2026, 9, 7, 7, 5, tzinfo=timezone.utc)  # 10:05 Istanbul
    assert session.candle_is_closed(friday_close_bar, "15m", monday_open)
    assert session.freshness(friday_close_bar, "15m", monday_open, 90) == "FRESH"
    assert not session.is_open(datetime(2026, 9, 6, 11, tzinfo=timezone.utc))


def test_indicator_references_and_insufficient_data():
    values = [Decimal(v) for v in range(1, 16)]
    assert ema(values, 5)[0] == Decimal("3")
    assert ema(values, 5)[-1].quantize(Decimal("0.0001")) == Decimal("13.0000")
    assert macd(values[:20]) is None
    assert atr([Decimal("11")] * 14, [Decimal("9")] * 14, [Decimal("10")] * 14) is None


def test_levels_do_not_look_at_future_candles():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prices = [(11, 9), (12, 10), (15, 12), (12, 10), (11, 8), (12, 10), (15, 12), (12, 10), (11, 8), (12, 10), (15, 12), (12, 10), (11, 8)]
    history = [candle(start + timedelta(hours=i), 11, high, low, 11) for i, (high, low) in enumerate(prices)]
    before = nearest_zones(history, Decimal("11"))
    future = candle(start + timedelta(hours=99), 11, 99, 1, 11, 999999)
    assert nearest_zones(history, Decimal("11")) == before
    assert future.timestamp > history[-1].timestamp


def test_pipeline_ignores_open_or_future_candles_in_every_timeframe():
    at = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    frames = {}
    for timeframe, step in (("1d", timedelta(days=1)), ("1h", timedelta(hours=1)), ("15m", timedelta(minutes=15))):
        rows = [candle(at - step * (70 - i), 100 + i / 10, 102 + i / 10, 98 + i / 10, 101 + i / 10, 1000 + i) for i in range(70)]
        rows.append(candle(at + step, 999, 1000, 998, 999, 999999, False))
        frames[timeframe] = rows
    result = analyze_frames("ASELS", frames, AppSettings(data_mode="mock"), "mock", at, False)
    context = result.details["analysis_context"]
    assert all(context[key] <= at for key in ("daily_candle_time", "hourly_candle_time", "entry_candle_time"))
    assert result.details["ignored_open_or_future_candles"] == {"1d": 1, "1h": 1, "15m": 1}


def test_live_mode_mock_source_and_stale_data_are_no_trade():
    decision, reason = decide(100, 82, True, "bullish", Decimal("2"), data_valid=False,
                              analysis_complete=True, session_valid=True, source_allowed=False)
    assert decision == "NO_TRADE"
    assert "data validation" in reason and "mock kaynak" in reason


def test_stop_distance_atr_sanity():
    args = (Decimal("5000"), Decimal("5000"), Decimal("100"))
    tiny = size_position(*args, Decimal("99.9"), Decimal("110"), Decimal(".005"), Decimal(".2"), Decimal(".1"), 0, 4,
                         Decimal("1.5"), Decimal("2"))
    far = size_position(*args, Decimal("90"), Decimal("120"), Decimal(".005"), Decimal(".2"), Decimal(".1"), 0, 4,
                        Decimal("1.5"), Decimal("2"))
    assert not tiny.approved and "aşırı küçük" in tiny.reason
    assert not far.approved and "aşırı uzak" in far.reason


def test_duplicate_order_partial_close_and_conservative_intrabar():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        portfolio = Portfolio(id=1, initial_balance=Decimal("5000"), cash_balance=Decimal("5000"), realized_pnl=Decimal(0))
        db.add(portfolio)
        db.commit()
        broker = PaperBroker(db, Decimal(".001"), Decimal(".0005"))
        position = broker.buy(portfolio, "ASELS", 4, Decimal("100"), Decimal("95"), Decimal("110"), "BREAKOUT", 90, "test", "same-key")
        for _ in range(5):
            with pytest.raises(DuplicateOrderError):
                broker.buy(portfolio, "ASELS", 1, Decimal("100"), Decimal("95"), Decimal("110"), "BREAKOUT", 90, "test", "same-key")
        assert len(db.scalars(select(Order).where(Order.side == "BUY")).all()) == 1
        first = broker.sell(portfolio, position, 2, Decimal("105"), "partial")
        assert position.status == "OPEN" and position.quantity == 2
        assert first.quantity == 2
        collision = candle(datetime.now(timezone.utc), 100, 112, 94, 101)
        second = broker.evaluate_candle(portfolio, position, collision)
        assert second.exit_reason.startswith("Aynı mum stop+hedef")
        assert second.exit_price < position.entry_price
        assert position.status == "CLOSED"
        trades = db.scalars(select(Trade)).all()
        assert sum(item.quantity for item in trades) == 4
        assert portfolio.cash_balance == Decimal("4998.80")
        assert portfolio.cash_balance == portfolio.initial_balance + portfolio.realized_pnl


def test_target_and_stop_execute_from_ohlc_and_insufficient_cash_is_rejected():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        portfolio = Portfolio(id=1, initial_balance=Decimal("5000"), cash_balance=Decimal("5000"), realized_pnl=Decimal(0))
        db.add(portfolio);db.commit()
        broker = PaperBroker(db, Decimal(".001"), Decimal(".0005"))
        with pytest.raises(ValueError, match="Yetersiz nakit"):
            broker.buy(portfolio, "BIG", 100, Decimal("100"), Decimal("95"), Decimal("110"), "BREAKOUT", 90, "test")
        target_position = broker.buy(portfolio, "TARGET", 1, Decimal("100"), Decimal("95"), Decimal("110"), "BREAKOUT", 90, "test")
        target_trade = broker.evaluate_candle(portfolio, target_position, candle(datetime.now(timezone.utc), 105, 111, 101, 108))
        assert target_trade.exit_reason == "Fixed take profit tetiklendi"
        stop_position = broker.buy(portfolio, "STOP", 1, Decimal("100"), Decimal("95"), Decimal("110"), "BREAKOUT", 90, "test")
        stop_trade = broker.evaluate_candle(portfolio, stop_position, candle(datetime.now(timezone.utc), 99, 100, 94, 96))
        assert stop_trade.exit_reason == "Fixed stop loss tetiklendi"


def test_replay_never_passes_future_candles_to_pipeline():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    end = datetime(2026, 9, 10, 15, tzinfo=timezone.utc)
    trigger = [candle(end - timedelta(minutes=15 * (64-i)), 100, 102, 98, 101, 1000) for i in range(65)]
    frames = {
        "15m": trigger,
        "1h": [candle(end - timedelta(hours=64-i)) for i in range(65)],
        "1d": [candle(end - timedelta(days=64-i)) for i in range(65)],
    }
    observed=[]
    session = BistMarketSession()
    def fake_pipeline(symbol, visible, config, source, analysis_at, require_market_session):
        observed.append((analysis_at, visible))
        return SimpleNamespace(decision="NO_TRADE", details={"analysis_complete":False},
                               funnel={"valid_setup":False,"score_pass":False,"rr_pass":False})
    with Session(engine, expire_on_commit=False) as db, patch("app.replay.engine.analyze_frames", side_effect=fake_pipeline):
        ReplayEngine(db, AppSettings(data_mode="live")).run("ASELS", frames, step=1)
    assert observed
    assert all(session.candle_is_closed(item.timestamp, timeframe, current)
               for current, visible in observed for timeframe, rows in visible.items() for item in rows)
