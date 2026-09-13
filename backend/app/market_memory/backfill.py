from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.market_data.symbols import BIST100_SYMBOLS
from app.market_data.yahoo_provider import YahooMarketDataProvider
from app.models import BackfillState, Candle, MarketStateSnapshot
from app.market_memory.service import MarketMemoryService, candle_quality
from app.services.collection_activity import log_activity
from app.services.historical_candles import BoundedTTLCache


_status_cache = BoundedTTLCache(max_entries=8)


class BackfillService:
    task = "HISTORICAL_CANDLES"

    def __init__(self, db, config, provider=None, symbols=None):
        self.db, self.config = db, config
        self.provider = provider or YahooMarketDataProvider()
        self.symbols = symbols or BIST100_SYMBOLS

    def status(self):
        cache_key=("backfill_status",id(self.db.get_bind()))
        cached=_status_cache.get(cache_key)
        if cached is not None:return cached
        states=self.db.scalars(select(BackfillState).order_by(BackfillState.task)).all()
        state=next((row for row in states if row.task==self.task),None)
        cursor=state.cursor if state else 0;total=len(self.symbols)
        completed=total if state and state.status=="COMPLETE" else min(cursor,total)
        frames=dict(self.db.execute(select(Candle.timeframe,func.count()).group_by(Candle.timeframe)).all())
        details=(state.details or {}) if state else {}
        result={"status":state.status if state else "PENDING","cursor":cursor,"universe_total":total,
            "completed_symbols":completed,"remaining_symbols":max(total-completed,0),
            "progress_pct":round(completed*100/total,1) if total else 0.0,
            "candle_count":sum(frames.values()),"last_symbol":details.get("last_symbol"),
            "last_timeframe":details.get("last_timeframe"),"last_run_at":state.last_run_at if state else None,
            "last_error":state.error if state else None,"timeframes":{key:frames.get(key,0) for key in ("5m","15m","1h","1d")},
            "tasks":states}
        _status_cache.put(cache_key,result,30)
        return result

    def run(self, batch_size=None):
        _status_cache.clear()
        size = min(max(1, batch_size or self.config.backfill_symbols_per_cycle), 5)
        state = self.db.get(BackfillState, self.task) or BackfillState(task=self.task)
        self.db.add(state)
        self.db.flush()
        if state.status == "COMPLETE":
            return {"status": "COMPLETE", "cursor": state.cursor, "processed_symbols": 0,
                "added_candles": 0, "errors": [], "quality": state.details.get("quality", {})}
        # Do not hold a database transaction while the provider performs network I/O.
        self.db.commit()
        total_added, errors, quality = 0, [], {}
        for offset in range(size):
            symbol = self.symbols[(state.cursor + offset) % len(self.symbols)]
            quality[symbol] = {}
            for timeframe in ("5m", "15m", "1h", "1d"):
                try:
                    before_added=total_added
                    rows = self.provider.get_candles(symbol, timeframe, self.config.backfill_candle_limit)
                    quality[symbol][timeframe] = candle_quality(rows, timeframe)
                    def key(value):
                        return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
                    candidate_times = [row.timestamp for row in rows]
                    existing = {key(value) for value in self.db.scalars(select(Candle.timestamp).where(
                        Candle.symbol == symbol, Candle.timeframe == timeframe,
                        Candle.timestamp.in_(candidate_times))).all()} if candidate_times else set()
                    for row in rows:
                        timestamp_key = key(row.timestamp)
                        if timestamp_key not in existing:
                            self.db.add(Candle(symbol=symbol, timeframe=timeframe, timestamp=row.timestamp, open=row.open,
                                high=row.high, low=row.low, close=row.close, volume=row.volume, source="yahoo_backfill"))
                            existing.add(timestamp_key); total_added += 1
                    log_activity(self.db,"BACKFILL",f"{symbol} {timeframe}","FETCH","INSERTED",
                        f"{total_added-before_added} candles")
                    # Short transactions let latency-sensitive API reads share the pool.
                    self.db.commit()
                except Exception as exc:
                    self.db.rollback()
                    errors.append({"symbol": symbol, "timeframe": timeframe, "error": type(exc).__name__})
                    log_activity(self.db,"BACKFILL",f"{symbol} {timeframe}","FETCH","ERROR",type(exc).__name__)
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
                            self.db.add(MarketStateSnapshot(**reconstructed))
                            log_activity(self.db,"MARKET_MEMORY",symbol,"RECONSTRUCT","RECORDED","15m snapshot")
                            self.db.commit()
            except Exception as exc:
                self.db.rollback(); errors.append({"symbol": symbol, "timeframe": "snapshot", "error": type(exc).__name__})
        state.cursor = (state.cursor + size) % len(self.symbols)
        state.processed_items = (state.processed_items or 0) + size
        state.last_run_at = datetime.now(timezone.utc)
        state.status = "PARTIAL" if errors else "COMPLETE" if state.cursor == 0 else "RUNNING"
        state.error = f"{len(errors)} bounded provider errors" if errors else None
        state.details = {"batch_size": size, "added_candles": total_added, "quality": quality,
            "cycle_complete": state.cursor == 0, "provider": "yahoo", "news_backfill": "SOURCE_LIMITED",
            "last_symbol":symbol,"last_timeframe":"1d"}
        self.db.commit()
        _status_cache.clear()
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
        _status_cache.clear()
        return {"status": "OK", "deleted": deleted}
