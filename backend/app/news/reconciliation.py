from __future__ import annotations

from app.news.service import NewsService


class NewsSymbolReconciliationService:
    """Bounded, restart-safe façade used by both API and the embedded worker."""

    def __init__(self, db, config):
        self.db = db
        self.config = config

    def run(self, batch_size: int | None = None) -> dict:
        return NewsService(self.db, self.config).reconcile_symbols(
            batch_size or self.config.news_reconcile_batch_size
        )
