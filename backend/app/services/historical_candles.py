from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import logging
from threading import RLock
from time import monotonic, perf_counter

from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc, select

from app.analysis.indicators import indicator_series
from app.models import Candle


INDICATOR_WARMUP_ROWS = 250
logger = logging.getLogger("HISTORICAL_READ")


@dataclass(frozen=True)
class CandleReadResult:
    payload: list[dict]
    timings_ms: dict[str, float]
    query_count: int
    cache_status: str


class BoundedTTLCache:
    def __init__(self, max_entries: int = 128):
        self.max_entries = max_entries
        self._items: OrderedDict[tuple, tuple[float, object]] = OrderedDict()
        self._lock = RLock()
        self.hits = self.misses = self.evictions = 0

    def get(self, key: tuple):
        now = monotonic()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._items.pop(key, None)
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: tuple, value, ttl_seconds: int):
        with self._lock:
            self._items[key] = (monotonic() + ttl_seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)
                self.evictions += 1

    def clear(self):
        with self._lock:
            self._items.clear()
            self.hits = self.misses = self.evictions = 0

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._items), "max_entries": self.max_entries,
                    "hits": self.hits, "misses": self.misses, "evictions": self.evictions}


candle_read_cache = BoundedTTLCache(max_entries=32)


def _stamp(value: datetime | None):
    return value.isoformat() if value else None


def _cache_key(db, symbol, timeframe, limit, at, start, end):
    return (id(db.get_bind()), symbol, timeframe, limit, _stamp(at), _stamp(start), _stamp(end))


def invalidate_candle_cache() -> None:
    candle_read_cache.clear()


def historical_candles(db, symbol: str, timeframe: str, limit: int,
                       at: datetime | None = None, start: datetime | None = None,
                       end: datetime | None = None) -> CandleReadResult:
    """Read a bounded display window plus a bounded indicator warm-up window."""
    total_started = perf_counter()
    symbol = symbol.upper()
    key = _cache_key(db, symbol, timeframe, limit, at, start, end)
    cached = candle_read_cache.get(key)
    if cached is not None:
        return CandleReadResult(cached, {"db": 0.0, "indicator": 0.0, "serialize": 0.0,
            "total": round((perf_counter()-total_started)*1000, 3)}, 0, "HIT")

    columns = (Candle.timestamp, Candle.open, Candle.high, Candle.low,
               Candle.close, Candle.volume, Candle.source)
    stmt = select(*columns).where(Candle.symbol == symbol, Candle.timeframe == timeframe)
    if start: stmt = stmt.where(Candle.timestamp >= start)
    if end: stmt = stmt.where(Candle.timestamp <= end)
    if at: stmt = stmt.where(Candle.timestamp <= at)
    db_started = perf_counter()
    display_desc = db.execute(stmt.order_by(desc(Candle.timestamp)).limit(limit)).all()
    query_count = 1
    warmup_desc = []
    if display_desc:
        oldest = display_desc[-1].timestamp
        warmup_desc = db.execute(select(*columns).where(
            Candle.symbol == symbol, Candle.timeframe == timeframe, Candle.timestamp < oldest
        ).order_by(desc(Candle.timestamp)).limit(INDICATOR_WARMUP_ROWS)).all()
        query_count += 1
    db_ms = (perf_counter()-db_started)*1000

    display = list(reversed(display_desc))
    calculation_rows = list(reversed(warmup_desc)) + display
    indicator_started = perf_counter()
    series = indicator_series(calculation_rows)
    display_series = series[-len(display):] if display else []
    indicator_ms = (perf_counter()-indicator_started)*1000
    serialize_started = perf_counter()
    payload = jsonable_encoder([{**{name: getattr(row, name) for name in
        ("timestamp", "open", "high", "low", "close", "volume", "source")},
        "indicators": display_series[index]} for index, row in enumerate(display)],
        custom_encoder={})
    serialize_ms = (perf_counter()-serialize_started)*1000
    # Anchored/ranged history is effectively immutable; latest views refresh quickly.
    if len(payload) <= 1000:
        candle_read_cache.put(key, payload, 300 if at or end else 30)
    timings = {
        "db": round(db_ms, 3), "indicator": round(indicator_ms, 3),
        "serialize": round(serialize_ms, 3), "total": round((perf_counter()-total_started)*1000, 3),
    }
    log = logger.warning if timings["total"] >= 1000 else logger.info
    log("historical_candle_read", extra={"symbol": symbol, "timeframe": timeframe,
        "row_limit": limit, "returned_rows": len(payload), "query_count": query_count,
        "cache_status": "MISS", **{f"{name}_ms": value for name, value in timings.items()}})
    return CandleReadResult(payload, timings, query_count, "MISS")
