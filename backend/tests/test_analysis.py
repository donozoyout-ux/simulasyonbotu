from decimal import Decimal
from datetime import datetime, timedelta, timezone

from app.analysis.market_structure import analyze_market_structure
from app.analysis.support_resistance import nearest_zones
from app.market_data.provider import CandleData
from app.strategy.scoring_engine import calculate_score


def test_bullish_market_structure():
    highs = list(map(Decimal, [10, 11, 15, 12, 11, 13, 17, 14, 13, 15, 19, 16, 15]))
    lows = list(map(Decimal, [8, 9, 11, 9, 8, 10, 13, 11, 10, 12, 15, 13, 12]))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [CandleData(start + timedelta(hours=i), lows[i], highs[i], lows[i], highs[i] - 1, Decimal(1000), True) for i in range(len(highs))]
    result = analyze_market_structure(candles)
    assert result["label"] == "BULLISH"
    assert result["sequence"] == ["HH", "HL"]
    assert all(swing["timestamp"] <= candles[-1].timestamp for swing in result["swings"])


def test_support_resistance_uses_prior_points():
    highs = list(map(Decimal, [11, 12, 15, 12, 11, 12, 15.05, 12, 11, 12, 15.02, 12, 11]))
    lows = list(map(Decimal, [9, 10, 12, 10, 8, 10, 12, 10, 8.02, 10, 12, 10, 8.01]))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [CandleData(start + timedelta(hours=i), Decimal(11), highs[i], lows[i], Decimal(11), Decimal(1000), True) for i in range(len(highs))]
    result = nearest_zones(candles, Decimal(11))
    assert result["support"] < Decimal(12)
    assert result["resistance"] > Decimal(12)
    assert result["support_zone"]["touch_count"] >= 2
    assert result["resistance_zone"]["touch_count"] >= 2


def test_score_is_capped_and_totals_100():
    score, breakdown = calculate_score({key: 1 for key in ("trend", "structure", "momentum", "volume", "levels", "setup", "risk_reward")})
    assert score == 100 and sum(breakdown.values()) == 100
