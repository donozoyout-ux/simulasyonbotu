from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math
import random

from app.market_data.provider import CandleData, MarketDataProvider
from app.market_data.symbols import BIST100_SYMBOLS


class MockMarketDataProvider(MarketDataProvider):
    name = "mock"

    def get_symbols(self) -> list[str]:
        return BIST100_SYMBOLS.copy()

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[CandleData]:
        minutes = {"15m": 15, "1h": 60, "1d": 1440}[timeframe.lower()]
        rng = random.Random(f"{symbol}:{timeframe}:v1")
        base = Decimal(str(30 + sum(map(ord, symbol)) % 220))
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        result = []
        price = base
        for i in range(limit, 0, -1):
            drift = Decimal(str(0.0012 + math.sin((limit-i)/13) * 0.0008))
            noise = Decimal(str(rng.uniform(-0.012, 0.012)))
            open_price = price
            close = max(Decimal("1"), price * (Decimal("1") + drift + noise))
            high = max(open_price, close) * Decimal(str(1 + rng.uniform(0.001, 0.009)))
            low = min(open_price, close) * Decimal(str(1 - rng.uniform(0.001, 0.009)))
            volume = Decimal(str(int(300000 + rng.random() * 1400000)))
            if i == 1:
                volume *= Decimal("1.8")
            result.append(CandleData(now - timedelta(minutes=minutes*i), open_price, high, low, close, volume, True))
            price = close
        return result

