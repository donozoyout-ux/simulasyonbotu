from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import monotonic
from threading import Lock
import httpx

from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.market_data.market_session import BistMarketSession
from app.models import BackfillState, NewsItem, NewsMarketReaction, NewsSourceState
from app.news.dedupe import content_hash
from app.news.sentiment import GroqNewsAnalyzer
from app.news.sources import KapSource, RssSource
from app.news.telegram import TelegramNewsNotifier

_REFRESH_LOCK=Lock()
_LAST_REFRESH_AT=0.0
_LAST_REFRESH_BIND=None


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

    def archive(self, symbol=None, source=None, category=None, sentiment=None, min_importance=None,
                start=None, end=None, overnight_only=False, reaction_only=False, limit=500):
        stmt = select(NewsItem, NewsMarketReaction).outerjoin(
            NewsMarketReaction,
            (NewsMarketReaction.news_id == NewsItem.id) & (NewsMarketReaction.symbol == NewsItem.symbol),
        )
        if symbol: stmt = stmt.where(NewsItem.symbol == symbol.upper())
        if source: stmt = stmt.where(NewsItem.source == source.upper())
        if category: stmt = stmt.where(NewsItem.category == category.upper())
        if sentiment: stmt = stmt.where(NewsItem.ai_sentiment == sentiment.upper())
        if min_importance is not None: stmt = stmt.where(NewsItem.ai_importance >= min_importance)
        if start: stmt = stmt.where(NewsItem.published_at >= start)
        if end: stmt = stmt.where(NewsItem.published_at <= end)
        if overnight_only: stmt = stmt.where(NewsItem.overnight_news.is_(True))
        if reaction_only: stmt = stmt.where(NewsMarketReaction.id.is_not(None))
        rows = self.db.execute(stmt.order_by(desc(NewsItem.published_at)).limit(limit)).all()
        result = []
        for item, reaction in rows:
            payload = {column.name: getattr(item, column.name) for column in NewsItem.__table__.columns}
            payload["reaction"] = ({column.name: getattr(reaction, column.name)
                for column in NewsMarketReaction.__table__.columns} if reaction else None)
            result.append(payload)
        return result

    def _news_backfill_state(self, now):
        state = self.db.get(BackfillState, "NEWS_ARCHIVE") or BackfillState(task="NEWS_ARCHIVE")
        state.status = "SOURCE_LIMITED"
        state.last_run_at = now
        state.processed_items = self.db.scalar(select(func.count()).select_from(NewsItem)) or 0
        state.details = {"requested_days": self.config.news_backfill_days,
            "mode": "incremental_feed_archive", "historical_source": "SOURCE_LIMITED"}
        self.db.add(state)
        return state

    def refresh(self) -> dict:
        global _LAST_REFRESH_AT, _LAST_REFRESH_BIND
        if not self.config.news_enabled:
            return {"status": "DISABLED", "new_items": 0, "errors": []}
        with _REFRESH_LOCK:
            now=monotonic()
            bind = self.db.get_bind()
            if bind is _LAST_REFRESH_BIND and now-_LAST_REFRESH_AT<60:
                return {"status":"RATE_LIMITED","new_items":0,"errors":[]}
            _LAST_REFRESH_AT=now
            _LAST_REFRESH_BIND=bind
        total, errors = 0, []
        fetched_at = datetime.now(timezone.utc)
        session = BistMarketSession.from_config(self.config)
        for source in self.sources:
            state = self.db.get(NewsSourceState, source.name) or NewsSourceState(source=source.name)
            self.db.add(state)
            try:
                records = source.fetch()
                added = 0
                for record in records:
                    digest = content_hash(record.source, record.title, record.content, record.url)
                    exists = self.db.scalar(select(NewsItem).where(or_(NewsItem.content_hash == digest,
                        (NewsItem.source == record.source) & (NewsItem.source_id == record.source_id))))
                    if exists:
                        exists.updated_at = fetched_at
                        continue
                    item = NewsItem(**record.__dict__, content_hash=digest, first_seen_at=fetched_at,
                        fetched_at=fetched_at, updated_at=fetched_at,
                        overnight_news=not session.is_open(record.published_at))
                    result = self.analyzer.evaluate(record.__dict__)
                    item.ai_status, item.ai_model = result["status"], result.get("model")
                    if result["status"] == "OK":
                        item.ai_sentiment, item.ai_importance = result["sentiment"], result["importance"]
                        item.ai_summary, item.ai_horizon = result["summary"], result["horizon"]
                        item.ai_risks, item.ai_tags = result["risks"], result["tags"]
                    try:
                        with self.db.begin_nested():
                            self.db.add(item)
                            self.db.flush()
                    except IntegrityError:
                        continue
                    if self.notifier.send(item):
                        item.telegram_sent = True
                        item.telegram_sent_at = fetched_at
                    added += 1
                state.status = "OK" if records else "NO_NEWS"
                state.last_fetch_at, state.new_items = datetime.now(timezone.utc), added
                state.last_seen_id = records[0].source_id if records else state.last_seen_id
                state.error = None
                total += added
                self._news_backfill_state(fetched_at)
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                state = self.db.get(NewsSourceState, source.name) or NewsSourceState(source=source.name)
                waf_blocked = isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500
                state.status, state.last_fetch_at = ("WAF_BLOCKED" if source.name == "KAP" and waf_blocked else "ERROR"), datetime.now(timezone.utc)
                state.error = f"{type(exc).__name__}: source unavailable"[:500]
                if isinstance(exc, (ValueError, TypeError)): state.parse_errors = (state.parse_errors or 0) + 1
                self.db.add(state); self.db.commit(); errors.append({"source": source.name, "error": state.error})
        return {"status": "OK" if not errors else "PARTIAL", "new_items": total, "errors": errors}

    def health(self) -> dict:
        states = self.db.scalars(select(NewsSourceState).order_by(NewsSourceState.source)).all()
        processed = self.db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.ai_status == "OK")) or 0
        pending = self.db.scalar(select(func.count()).select_from(NewsItem).where(or_(NewsItem.ai_status.is_(None),NewsItem.ai_status.in_(["AI_UNAVAILABLE", "RATE_LIMITED"])))) or 0
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        last_24h = self.db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.published_at >= since)) or 0
        important = self.db.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.published_at >= since, NewsItem.ai_importance >= self.config.news_telegram_min_importance)) or 0
        overnight = self.db.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.published_at >= since, NewsItem.overnight_news.is_(True))) or 0
        kap = self.db.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.published_at >= since, NewsItem.source == "KAP")) or 0
        failures = {"ERROR", "WAF_BLOCKED"}
        return {"enabled": self.config.news_enabled, "configured": True,
            "status": "NO_DATA" if not states else "ERROR" if all(x.status in failures for x in states) else "PARTIAL" if any(x.status in failures for x in states) else "OK",
            "sources": {x.source: {"status": x.status, "last_fetch": x.last_fetch_at, "new_items": x.new_items,
                "parse_errors": x.parse_errors, "error": x.error} for x in states},
            "groq_processed": processed, "pending_ai": pending, "last_24h": last_24h,
            "important_24h": important, "overnight_24h": overnight, "kap_24h": kap,
            "errors": sum(1 for state in states if state.status in failures)}
