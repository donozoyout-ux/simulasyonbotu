from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from app.market_data.provider import DataValidationError, normalize_candles


def row(ts, close=10): return {"timestamp": ts, "open": 10, "high": 11, "low": 9, "close": close, "volume": 100, "closed": True}


def test_sorts_and_normalizes_numeric_values():
    now = datetime.now(timezone.utc)
    result = normalize_candles([row(now-timedelta(minutes=2)), row(now-timedelta(minutes=3))], now)
    assert result[0].timestamp < result[1].timestamp
    assert isinstance(result[0].close, Decimal)


def test_rejects_duplicate_future_and_bad_price():
    now = datetime.now(timezone.utc); past = now-timedelta(minutes=2)
    with pytest.raises(DataValidationError): normalize_candles([row(past), row(past)], now)
    with pytest.raises(DataValidationError): normalize_candles([row(now+timedelta(minutes=1))], now)
    with pytest.raises(DataValidationError): normalize_candles([row(past, 0)], now)


def test_excludes_incomplete_candle():
    now = datetime.now(timezone.utc)
    incomplete = row(now-timedelta(minutes=1)); incomplete["closed"] = False
    assert normalize_candles([incomplete], now) == []


def test_rejects_string_values_and_equivalent_timezone_duplicates():
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=2)
    invalid = row(past)
    invalid["close"] = "10.0"
    with pytest.raises(DataValidationError):
        normalize_candles([invalid], now)
    same_in_offset = past.astimezone(timezone(timedelta(hours=3)))
    with pytest.raises(DataValidationError):
        normalize_candles([row(past), row(same_in_offset)], now)
