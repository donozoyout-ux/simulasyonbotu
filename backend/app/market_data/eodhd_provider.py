from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app.market_data.provider import CandleData, DataValidationError, MarketDataProvider, normalize_candles
from app.market_data.symbols import BIST100_SYMBOLS


class EodhdHistoricalProvider(MarketDataProvider):
    """Authenticated historical adapter; never falls back to synthetic data.

    EODHD exposes 5m intraday bars. Three consecutive bars are deterministically
    aggregated for the strategy's 15m timeframe. Availability still depends on
    the account's exchange/data entitlement.
    """
    name = "eodhd"
    price_adjustment = "none"

    def __init__(self, api_token: str, start: datetime | None = None, end: datetime | None = None, exchange: str = "IS"):
        if not api_token: raise DataValidationError("EODHD_API_TOKEN gerekli")
        self.end = end or datetime.now(timezone.utc)
        self.start = start or self.end - timedelta(days=120)
        self.api_token, self.exchange = api_token, exchange

    def get_symbols(self) -> list[str]:
        return BIST100_SYMBOLS.copy()

    def _request(self, path: str, params: dict) -> list[dict]:
        response = httpx.get(f"https://eodhd.com/api/{path}", params={**params, "api_token": self.api_token,
            "fmt": "json"}, headers={"User-Agent": "BIST-Paper-Trading/4.0"}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list): raise DataValidationError(f"EODHD unexpected response: {payload}")
        return payload

    def _intraday(self, symbol: str, interval: str) -> list[CandleData]:
        ticker = f"{symbol}.{self.exchange}"
        rows = self._request(f"intraday/{ticker}", {"interval": interval,
            "from": int(self.start.timestamp()), "to": int(self.end.timestamp())})
        return normalize_candles([{"timestamp": int(row.get("timestamp") or row.get("datetime")),
            "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
            "volume": row["volume"], "closed": True} for row in rows], datetime.now(timezone.utc))

    @staticmethod
    def aggregate_15m(rows: list[CandleData]) -> list[CandleData]:
        groups = defaultdict(list)
        for candle in rows:
            stamp = candle.timestamp.astimezone(timezone.utc)
            bucket = stamp.replace(minute=(stamp.minute // 15) * 15, second=0, microsecond=0)
            groups[bucket].append(candle)
        output = []
        for stamp, candles in sorted(groups.items()):
            candles = sorted(candles, key=lambda item: item.timestamp)
            if len(candles) != 3: continue
            output.append(CandleData(stamp, candles[0].open, max(c.high for c in candles), min(c.low for c in candles),
                                     candles[-1].close, sum((c.volume for c in candles), Decimal(0)), True))
        return output

    def get_candles(self, symbol: str, timeframe: str, limit: int = 10000) -> list[CandleData]:
        if timeframe == "15m": candles = self.aggregate_15m(self._intraday(symbol, "5m"))
        elif timeframe == "1h": candles = self._intraday(symbol, "1h")
        elif timeframe == "1d":
            ticker = f"{symbol}.{self.exchange}"
            rows = self._request(f"eod/{ticker}", {"from": self.start.date().isoformat(), "to": self.end.date().isoformat()})
            candles = normalize_candles([{"timestamp": datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc),
                "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
                "volume": row["volume"], "closed": True} for row in rows], datetime.now(timezone.utc))
        else: raise DataValidationError(f"Desteklenmeyen timeframe: {timeframe}")
        if len(candles) < 35: raise DataValidationError(f"{symbol} {timeframe}: yetersiz OOS history")
        return candles[-limit:]
