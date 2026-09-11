from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math


@dataclass(frozen=True)
class CandleData:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    closed: bool = True


class DataValidationError(ValueError):
    pass


class MarketDataProvider(ABC):
    name = "abstract"

    @abstractmethod
    def get_symbols(self) -> list[str]: ...

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[CandleData]: ...

    def get_latest_price(self, symbol: str) -> Decimal:
        candles = self.get_candles(symbol, "15m", 2)
        if not candles:
            raise DataValidationError(f"{symbol}: fiyat verisi yok")
        return candles[-1].close


def normalize_candles(rows: list[dict], now: datetime | None = None) -> list[CandleData]:
    now = now or datetime.now(timezone.utc)
    normalized: dict[datetime, CandleData] = {}
    for row in rows:
        try:
            ts = row["timestamp"]
            if ts is None:
                raise DataValidationError("Timestamp null olamaz")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                ts = datetime.fromtimestamp(ts, timezone.utc)
            elif not isinstance(ts, datetime):
                raise DataValidationError("Timestamp datetime veya unix numeric olmalı")
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts = ts.astimezone(timezone.utc)
            raw_values = [row[k] for k in ("open", "high", "low", "close", "volume")]
            if any(isinstance(value, (str, bool)) or value is None for value in raw_values):
                raise DataValidationError("OHLCV alanları string/bool/null olamaz")
            values = [value if isinstance(value, Decimal) else Decimal(str(value)) for value in raw_values]
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise DataValidationError(f"Geçersiz OHLCV satırı: {row!r}") from exc
        if ts > now:
            raise DataValidationError("Gelecek zamanlı mum reddedildi")
        if any(not math.isfinite(float(v)) for v in values) or any(v <= 0 for v in values[:4]) or values[4] < 0:
            raise DataValidationError("NaN, sıfır veya negatif OHLCV değeri")
        o, h, low, c, volume = values
        if h < max(o, c) or low > min(o, c) or low > h:
            raise DataValidationError("Tutarsız OHLC aralığı")
        if ts in normalized:
            raise DataValidationError("Duplicate candle timestamp")
        closed = row.get("closed", True)
        if not isinstance(closed, bool):
            raise DataValidationError("closed alanı boolean olmalı")
        normalized[ts] = CandleData(ts, o, h, low, c, volume, closed)
    ordered = [normalized[k] for k in sorted(normalized)]
    return [c for c in ordered if c.closed]
