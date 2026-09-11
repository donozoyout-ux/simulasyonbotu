from decimal import Decimal
from app.analysis.indicators import atr, bollinger, ema, macd, rate_of_change, rsi


def d(values): return [Decimal(str(v)) for v in values]


def test_ema_is_deterministic_and_tracks_uptrend():
    values = d(range(1, 61))
    assert ema(values, 20)[-1] > ema(values, 50)[-1]
    assert ema(values, 20) == ema(values, 20)


def test_momentum_indicators():
    values = d(range(1, 61))
    assert rsi(values)[-1] == Decimal(100)
    assert macd(values)["macd"] > 0
    assert rate_of_change(values, 10) > 0


def test_atr_and_bollinger():
    closes = d(range(10, 50)); highs = [x+1 for x in closes]; lows = [x-1 for x in closes]
    assert atr(highs, lows, closes) == Decimal(2)
    bands = bollinger(closes)
    assert bands["upper"] > bands["middle"] > bands["lower"]

