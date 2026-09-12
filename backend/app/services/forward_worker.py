from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.market_data.market_session import BistMarketSession
from app.models import Position, ProcessedCandle, ScanRun
from app.scanner.bist_scanner import BistScanner
from app.services.benchmark import fetch_xu100_price
from app.services.forward_test import ensure_forward_run


def expected_closed_candle(config, now: datetime | None = None):
    """
    Return the latest fully closed 15m strategy candle.

    The worker itself can run more often (5m during market hours), while the
    frozen strategy still consumes only one new 15m signal candle at a time.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    session = BistMarketSession.from_config(config)
    local = now.astimezone(session.tz)
    if (
        not session.is_trading_day(local.date())
        or local.time() < session.open_time
        or local.time() > session.close_time
    ):
        return None
    opened = datetime.combine(local.date(), session.open_time, session.tz)
    elapsed = (local - opened).total_seconds()
    completed = int(elapsed // 900)
    if completed < 1:
        return None
    return (opened + timedelta(minutes=(completed - 1) * 15)).astimezone(timezone.utc)


class ForwardWorker:
    def __init__(self, db, config, provider=None):
        self.db, self.config, self.provider = db, config, provider

    def _refresh_benchmark(self, run) -> dict:
        try:
            price, price_time = fetch_xu100_price()
            if run.benchmark_start_price is None:
                run.benchmark_start_price = price
            run.benchmark_latest_price = price
            run.benchmark_updated_at = price_time
            self.db.commit()
            return {
                "status": "ok",
                "symbol": "XU100",
                "price": float(price),
                "updated_at": price_time.isoformat(),
            }
        except Exception as exc:
            self.db.rollback()
            return {
                "status": "unavailable",
                "symbol": "XU100",
                "error": str(exc),
            }

    def run_once(self, now: datetime | None = None, max_symbols: int | None = None):
        if self.config.operation_mode != "LIVE_PAPER":
            return {"status": "wrong_mode"}

        now = now or datetime.now(timezone.utc)
        session = BistMarketSession.from_config(self.config)
        market_open = session.is_open(now)
        run = ensure_forward_run(self.db, self.config, now)

        # Always-on maintenance: benchmark refresh happens even after the market closes.
        benchmark = self._refresh_benchmark(run)

        # Outside the session there are no new paper entries/exits. The worker remains
        # alive and performs maintenance on the slower after-hours cadence.
        if not market_open:
            return {
                "status": "market_closed",
                "maintenance": "after_hours",
                "market_open": False,
                "benchmark": benchmark,
            }

        scanner = BistScanner(self.db, self.config, self.provider)

        # Paper positions are checked every worker cycle using closed 5m candles.
        # This improves stop/target reaction without changing the frozen 15m strategy.
        position_check = scanner.manage_positions_only("5m")

        stamp = expected_closed_candle(self.config, now)
        if stamp is None:
            return {
                "status": "market_open_waiting_for_first_15m_close",
                "market_open": True,
                "position_check": position_check,
                "benchmark": benchmark,
            }

        marker = self.db.scalar(
            select(ProcessedCandle).where(
                ProcessedCandle.run_id == run.run_id,
                ProcessedCandle.timeframe == "15m",
                ProcessedCandle.closed_candle_timestamp == stamp,
            )
        )
        if marker and marker.status == "COMPLETE":
            return {
                "status": "already_processed",
                "maintenance": "five_minute",
                "closed_candle_timestamp": stamp.isoformat(),
                "position_check": position_check,
                "benchmark": benchmark,
            }

        if not marker:
            marker = ProcessedCandle(
                run_id=run.run_id,
                timeframe="15m",
                closed_candle_timestamp=stamp,
                status="STARTED",
            )
            self.db.add(marker)
            try:
                self.db.commit()
                self.db.refresh(marker)
            except IntegrityError:
                self.db.rollback()
                return {
                    "status": "already_processing",
                    "closed_candle_timestamp": stamp.isoformat(),
                    "position_check": position_check,
                    "benchmark": benchmark,
                }

        try:
            result = scanner.run(max_symbols or self.config.scanner_symbol_limit, stamp)
            scan = self.db.scalar(
                select(ScanRun)
                .where(
                    ScanRun.run_id == run.run_id,
                    ScanRun.closed_candle_timestamp == stamp,
                )
                .order_by(ScanRun.id.desc())
                .limit(1)
            )
            marker.status = "COMPLETE"
            marker.error = None
            marker.scan_run_id = scan.id if scan else None
            self.db.commit()
            return {
                **result,
                "closed_candle_timestamp": stamp.isoformat(),
                "position_check": position_check,
                "benchmark": benchmark,
            }
        except Exception as exc:
            self.db.rollback()
            marker = self.db.get(ProcessedCandle, marker.id)
            marker.status = "FAILED"
            marker.error = str(exc)
            self.db.commit()
            raise
