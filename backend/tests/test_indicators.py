from decimal import Decimal
from app.analysis.indicators import atr, bollinger, ema, indicator_series, indicator_snapshot, macd, rate_of_change, rsi, vwap
from app.market_data.provider import CandleData
from datetime import datetime, timedelta, timezone


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


def test_extended_indicator_snapshot_has_finite_backend_values():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    candles=[CandleData(start+timedelta(days=i),Decimal(100+i),Decimal(102+i),Decimal(99+i),Decimal(101+i),Decimal(1000+i),True) for i in range(220)]
    values=indicator_snapshot(candles)
    assert values["ema20"]>values["ema50"]>values["ema200"]
    assert 0<=values["rsi"]<=100 and values["macd"]["histogram"] is not None
    assert values["bollinger"]["upper"]>values["bollinger"]["lower"]
    assert values["atr"]>0 and values["vwap"]>0 and values["rvol"]>0
    assert values["volatility_20d"]>=0


def test_vwap_rejects_zero_volume():
    assert vwap(d([1]),d([1]),d([1]),d([0])) is None


def test_linear_indicator_series_matches_every_prefix_snapshot():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    candles=[CandleData(start+timedelta(minutes=15*i),Decimal(100+i%17),Decimal(102+i%17),
        Decimal(99+i%17),Decimal(101+i%17),Decimal(1000+i),True) for i in range(260)]
    series=indicator_series(candles)
    for index in (0, 13, 14, 19, 25, 33, 49, 199, 259):
        expected=indicator_snapshot(candles[:index+1])
        assert {key:value for key,value in series[index].items() if key!="timestamp"} == expected

