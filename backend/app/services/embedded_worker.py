from __future__ import annotations

import logging
import threading
from time import monotonic
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.market_data.market_session import BistMarketSession
from app.services.forward_worker import ForwardWorker
from app.news.service import NewsService
from app.market_memory.backfill import BackfillService
from app.market_memory.service import MarketMemoryService
from app.market_memory.brief import MorningBriefService

logger = logging.getLogger("EMBEDDED_WORKER")


class EmbeddedWorker:
    """
    Single-process scheduler for the Render web service.

    Market open: run every 5 minutes.
    After hours/weekends: run every 15 minutes.
    The strategy itself still consumes one new closed 15m candle at a time.
    """

    def __init__(self, config):
        self.config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_news_poll: float | None = None

    def _sleep_seconds(self) -> int:
        session = BistMarketSession.from_config(self.config)
        if session.is_open(datetime.now(timezone.utc)):
            return max(60, int(self.config.market_open_poll_seconds))
        return max(60, int(self.config.after_hours_poll_seconds))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = self.run_cycle()
                logger.info("embedded_worker_cycle", extra={"result": result.get("status")})
            except Exception:
                logger.exception("embedded_worker_cycle_failed")

            self._stop.wait(self._sleep_seconds())

    def run_cycle(self) -> dict:
        with SessionLocal() as db:
            result = ForwardWorker(db, self.config).run_once()
            market_open = BistMarketSession.from_config(self.config).is_open()
            news_interval = (self.config.news_poll_minutes_open if market_open
                else self.config.news_poll_minutes_closed) * 60
            maintenance = {"market_open": market_open}
            if self.config.news_enabled and (self._last_news_poll is None or monotonic() - self._last_news_poll >= news_interval):
                try:
                    maintenance["news"] = NewsService(db, self.config).refresh()
                except Exception:
                    db.rollback(); logger.exception("news_refresh_failed")
                    maintenance["news"] = {"status": "ERROR"}
                self._last_news_poll = monotonic()
            if self.config.backfill_enabled:
                try:
                    backfill = BackfillService(db, self.config)
                    maintenance["backfill"] = backfill.run()
                    maintenance["retention"] = backfill.maintain_retention()
                except Exception:
                    db.rollback(); logger.exception("backfill_failed")
                    maintenance["backfill"] = {"status": "ERROR"}
            if self.config.market_memory_enabled:
                try:
                    maintenance["reactions"] = MarketMemoryService(db, self.config).evaluate_reactions(
                        self.config.market_memory_reaction_batch)
                except Exception:
                    db.rollback(); logger.exception("reaction_evaluation_failed")
                    maintenance["reactions"] = {"status": "ERROR"}
            try:
                maintenance["morning_brief"] = MorningBriefService(db, self.config).run()
            except Exception:
                db.rollback(); logger.exception("morning_brief_failed")
                maintenance["morning_brief"] = {"status": "ERROR"}
            return {**result, "maintenance": maintenance}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop,
            name="bist-embedded-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info("embedded_worker_started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("embedded_worker_stopped")
