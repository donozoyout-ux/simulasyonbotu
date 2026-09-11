from __future__ import annotations

import logging
import threading
from time import monotonic
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.market_data.market_session import BistMarketSession
from app.services.forward_worker import ForwardWorker
from app.news.service import NewsService

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
        self._last_news_poll = 0.0

    def _sleep_seconds(self) -> int:
        session = BistMarketSession.from_config(self.config)
        if session.is_open(datetime.now(timezone.utc)):
            return max(60, int(self.config.market_open_poll_seconds))
        return max(60, int(self.config.after_hours_poll_seconds))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with SessionLocal() as db:
                    result = ForwardWorker(db, self.config).run_once()
                    news_interval=(self.config.news_poll_minutes_open if BistMarketSession.from_config(self.config).is_open()
                        else self.config.news_poll_minutes_closed)*60
                    if self.config.news_enabled and monotonic()-self._last_news_poll>=news_interval:
                        try: NewsService(db,self.config).refresh()
                        except Exception: db.rollback();logger.exception("news_refresh_failed")
                        self._last_news_poll=monotonic()
                logger.info("embedded_worker_cycle", extra={"result": result.get("status")})
            except Exception:
                logger.exception("embedded_worker_cycle_failed")

            self._stop.wait(self._sleep_seconds())

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
