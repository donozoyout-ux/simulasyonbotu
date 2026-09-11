from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.market_data.provider import DataValidationError, MarketDataProvider, normalize_candles
from app.market_data.market_session import BistMarketSession
from app.market_data.symbols import BIST100_SYMBOLS


class YahooMarketDataProvider(MarketDataProvider):
    """Free Yahoo chart endpoint adapter. It is not a buy/sell signal service."""

    name = "yahoo"
    price_adjustment = "provider_chart_raw_unverified"
    intervals = {"15m": ("15m", "60d"), "1h": ("60m", "730d"), "1d": ("1d", "5y")}

    def get_symbols(self) -> list[str]:
        return BIST100_SYMBOLS.copy()

    def __init__(self, session: BistMarketSession | None = None):
        self.session = session or BistMarketSession()

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200):
        if timeframe.lower() not in self.intervals:
            raise DataValidationError(f"Desteklenmeyen timeframe: {timeframe}")
        interval, range_ = self.intervals[timeframe.lower()]
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.IS"
        response = httpx.get(url, params={"interval": interval, "range": range_}, headers={"User-Agent": "Mozilla/5.0 BIST-Paper-Trading/1.0"}, timeout=15)
        response.raise_for_status()
        payload = response.json()["chart"]["result"][0]
        timestamps = payload.get("timestamp") or []
        quote = payload["indicators"]["quote"][0]
        rows = []
        now = datetime.now(timezone.utc)
        for idx, ts in enumerate(timestamps):
            values = {key: quote.get(key, [None] * len(timestamps))[idx] for key in ("open", "high", "low", "close", "volume")}
            if any(value is None for value in values.values()):
                continue
            candle_time = datetime.fromtimestamp(ts, timezone.utc)
            rows.append({"timestamp": ts, **values, "closed": self.session.candle_is_closed(candle_time, timeframe.lower(), now)})
        candles = normalize_candles(rows, now)
        if len(candles) < 35:
            raise DataValidationError(f"{symbol} {timeframe}: yetersiz kapanmış mum")
        return candles[-limit:]
