from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic
from threading import Lock

from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.models import NewsItem, NewsSourceState
from app.news.dedupe import content_hash
from app.news.sentiment import GroqNewsAnalyzer
from app.news.sources import KapSource, RssSource
from app.news.telegram import TelegramNewsNotifier

_REFRESH_LOCK=Lock()
_LAST_REFRESH_AT=0.0


class NewsService:
    def __init__(self, db, config, sources=None, analyzer=None, notifier=None):
        self.db, self.config = db, config
        self.sources = sources if sources is not None else self._default_sources()
        self.analyzer = analyzer or GroqNewsAnalyzer(config)
        self.notifier = notifier or TelegramNewsNotifier(config)

    def _default_sources(self):
        common = {"timeout": self.config.news_http_timeout_seconds, "max_bytes": self.config.news_max_html_bytes}
        sources = []
        if self.config.kap_enabled:
            sources.append(KapSource(**common))
        sources.append(RssSource(**common))
        return sources

    def list(self, symbol=None, source=None, sentiment=None, min_importance=None, limit=100):
        stmt = select(NewsItem)
        if symbol: stmt = stmt.where(NewsItem.symbol == symbol.upper())
        if source: stmt = stmt.where(NewsItem.source == source.upper())
        if sentiment: stmt = stmt.where(NewsItem.ai_sentiment == sentiment.upper())
        if min_importance is not None: stmt = stmt.where(NewsItem.ai_importance >= min_importance)
        return self.db.scalars(stmt.order_by(desc(NewsItem.published_at)).limit(limit)).all()

    def refresh(self) -> dict:
        global _LAST_REFRESH_AT
        if not self.config.news_enabled:
            return {"status": "DISABLED", "new_items": 0, "errors": []}
        with _REFRESH_LOCK:
            now=monotonic()
            if now-_LAST_REFRESH_AT<60:
                return {"status":"RATE_LIMITED","new_items":0,"errors":[]}
            _LAST_REFRESH_AT=now
        total, errors = 0, []
        for source in self.sources:
            state = self.db.get(NewsSourceState, source.name) or NewsSourceState(source=source.name)
            self.db.add(state)
            try:
                records = source.fetch()
                added = 0
                for record in records:
                    digest = content_hash(record.source, record.title, record.content, record.url)
                    exists = self.db.scalar(select(NewsItem.id).where(or_(NewsItem.content_hash == digest,
                        (NewsItem.source == record.source) & (NewsItem.source_id == record.source_id))))
                    if exists: continue
                    item = NewsItem(**record.__dict__, content_hash=digest)
                    result = self.analyzer.evaluate(record.__dict__)
                    item.ai_status, item.ai_model = result["status"], result.get("model")
                    if result["status"] == "OK":
                        item.ai_sentiment, item.ai_importance = result["sentiment"], result["importance"]
                        item.ai_summary, item.ai_horizon = result["summary"], result["horizon"]
                        item.ai_risks, item.ai_tags = result["risks"], result["tags"]
                    self.db.add(item)
                    try:
                        self.db.flush()
                    except IntegrityError:
                        self.db.rollback()
                        continue
                    if self.notifier.send(item): item.telegram_sent = True
                    added += 1
                state.status = "OK" if records else "NO_NEWS"
                state.last_fetch_at, state.new_items = datetime.now(timezone.utc), added
                state.last_seen_id = records[0].source_id if records else state.last_seen_id
                state.error = None
                total += added
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                state = self.db.get(NewsSourceState, source.name) or NewsSourceState(source=source.name)
                state.status, state.last_fetch_at = "ERROR", datetime.now(timezone.utc)
                state.error = f"{type(exc).__name__}: source unavailable"[:500]
                if isinstance(exc, (ValueError, TypeError)): state.parse_errors = (state.parse_errors or 0) + 1
                self.db.add(state); self.db.commit(); errors.append({"source": source.name, "error": state.error})
        return {"status": "OK" if not errors else "PARTIAL", "new_items": total, "errors": errors}

    def health(self) -> dict:
        states = self.db.scalars(select(NewsSourceState).order_by(NewsSourceState.source)).all()
        processed = self.db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.ai_status == "OK")) or 0
        pending = self.db.scalar(select(func.count()).select_from(NewsItem).where(or_(NewsItem.ai_status.is_(None),NewsItem.ai_status.in_(["AI_UNAVAILABLE", "RATE_LIMITED"])))) or 0
        return {"enabled": self.config.news_enabled, "configured": True,
            "status": "NO_DATA" if not states else "ERROR" if all(x.status == "ERROR" for x in states) else "PARTIAL" if any(x.status == "ERROR" for x in states) else "OK",
            "sources": {x.source: {"status": x.status, "last_fetch": x.last_fetch_at, "new_items": x.new_items,
                "parse_errors": x.parse_errors, "error": x.error} for x in states},
            "groq_processed": processed, "pending_ai": pending}
