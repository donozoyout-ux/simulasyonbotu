from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.market_data.symbols import BIST100_SYMBOLS
from app.market_data.yahoo_provider import YahooMarketDataProvider
from app.models import BackfillState, Candle, MarketStateSnapshot
from app.market_memory.service import MarketMemoryService, candle_quality


class BackfillService:
    task = "HISTORICAL_CANDLES"

    def __init__(self, db, config, provider=None, symbols=None):
        self.db, self.config = db, config
        self.provider = provider or YahooMarketDataProvider()
        self.symbols = symbols or BIST100_SYMBOLS

    def status(self):
        return self.db.scalars(select(BackfillState).order_by(BackfillState.task)).all()

    def run(self, batch_size=None):
        size = min(max(1, batch_size or self.config.backfill_symbols_per_cycle), 5)
        state = self.db.get(BackfillState, self.task) or BackfillState(task=self.task)
        self.db.add(state)
        self.db.flush()
        if state.status == "COMPLETE":
            return {"status": "COMPLETE", "cursor": state.cursor, "processed_symbols": 0,
                "added_candles": 0, "errors": [], "quality": state.details.get("quality", {})}
        total_added, errors, quality = 0, [], {}
        for offset in range(size):
            symbol = self.symbols[(state.cursor + offset) % len(self.symbols)]
            quality[symbol] = {}
            for timeframe in ("5m", "15m", "1h", "1d"):
                try:
                    rows = self.provider.get_candles(symbol, timeframe, self.config.backfill_candle_limit)
                    quality[symbol][timeframe] = candle_quality(rows, timeframe)
                    def key(value):
                        return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
                    existing = {key(value) for value in self.db.scalars(select(Candle.timestamp).where(
                        Candle.symbol == symbol, Candle.timeframe == timeframe)).all()}
                    for row in rows:
                        timestamp_key = key(row.timestamp)
                        if timestamp_key not in existing:
                            self.db.add(Candle(symbol=symbol, timeframe=timeframe, timestamp=row.timestamp, open=row.open,
                                high=row.high, low=row.low, close=row.close, volume=row.volume, source="yahoo_backfill"))
                            existing.add(timestamp_key); total_added += 1
                except Exception as exc:
                    errors.append({"symbol": symbol, "timeframe": timeframe, "error": type(exc).__name__})
            self.db.commit()
            try:
                latest = self.db.scalar(select(Candle.timestamp).where(Candle.symbol == symbol,
                    Candle.timeframe == "15m").order_by(Candle.timestamp.desc()).limit(1))
                if latest:
                    reconstructed = MarketMemoryService(self.db, self.config).snapshot_at(symbol, latest)
                    if isinstance(reconstructed, dict) and reconstructed.get("status") == "RECONSTRUCTED":
                        identity = self.db.scalar(select(MarketStateSnapshot.id).where(
                            MarketStateSnapshot.symbol == symbol, MarketStateSnapshot.timeframe == "15m",
                            MarketStateSnapshot.timestamp == reconstructed["timestamp"]))
                        if not identity:
                            self.db.add(MarketStateSnapshot(**reconstructed)); self.db.commit()
            except Exception as exc:
                self.db.rollback(); errors.append({"symbol": symbol, "timeframe": "snapshot", "error": type(exc).__name__})
        state.cursor = (state.cursor + size) % len(self.symbols)
        state.processed_items = (state.processed_items or 0) + size
        state.last_run_at = datetime.now(timezone.utc)
        state.status = "PARTIAL" if errors else "COMPLETE" if state.cursor == 0 else "RUNNING"
        state.error = f"{len(errors)} bounded provider errors" if errors else None
        state.details = {"batch_size": size, "added_candles": total_added, "quality": quality,
            "cycle_complete": state.cursor == 0, "provider": "yahoo", "news_backfill": "SOURCE_LIMITED"}
        self.db.commit()
        return {"status": state.status, "cursor": state.cursor, "processed_symbols": size,
            "added_candles": total_added, "errors": errors, "quality": quality}

    def maintain_retention(self):
        if not self.config.candle_retention_enabled:
            return {"status": "DISABLED", "deleted": 0}
        deleted = 0
        now = datetime.now(timezone.utc)
        policies = {
            "5m": self.config.candle_retention_5m_days,
            "15m": self.config.candle_retention_15m_days,
            "1h": self.config.candle_retention_1h_days,
            "1d": self.config.candle_retention_1d_days,
        }
        for timeframe, days in policies.items():
            if days <= 0:
                continue
            rows = self.db.scalars(select(Candle).where(
                Candle.timeframe == timeframe, Candle.timestamp < now - timedelta(days=days)
            )).all()
            for row in rows:
                self.db.delete(row)
            deleted += len(rows)
        self.db.commit()
        return {"status": "OK", "deleted": deleted}
