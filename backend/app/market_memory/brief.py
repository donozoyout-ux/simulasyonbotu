from datetime import datetime, time, timedelta, timezone

from sqlalchemy import desc, func, select

from app.market_data.market_session import BistMarketSession
from app.models import DecisionLog, MarketStateSnapshot, NewsItem, Position, WatchlistItem
from app.services.telegram import TelegramNotifier


class MorningBriefService:
    """Builds one factual, DB-backed pre-open brief without affecting execution."""

    def __init__(self, db, config, notifier=None):
        self.db, self.config = db, config
        self.notifier = notifier or TelegramNotifier(config)

    def run(self, now=None):
        now = now or datetime.now(timezone.utc)
        session = BistMarketSession.from_config(self.config)
        local = now.astimezone(session.tz)
        if not session.is_trading_day(local.date()) or not time(9, 15) <= local.time() < time(10, 0):
            return {"status": "NOT_DUE"}
        key = f"MORNING_BRIEF:{local.date().isoformat()}"
        if self.db.scalar(select(DecisionLog.id).where(DecisionLog.category == "MORNING_BRIEF",
            DecisionLog.reason == key)):
            return {"status": "DEDUPED"}
        since = datetime.combine(local.date() - timedelta(days=1), session.close_time, session.tz).astimezone(timezone.utc)
        news = self.db.scalars(select(NewsItem).where(NewsItem.published_at >= since,
            NewsItem.overnight_news.is_(True)).order_by(desc(NewsItem.ai_importance), desc(NewsItem.published_at)).limit(10)).all()
        trends = self.db.scalars(select(MarketStateSnapshot).order_by(
            desc(MarketStateSnapshot.relative_strength_1d), desc(MarketStateSnapshot.timestamp)).limit(5)).all()
        watch = self.db.scalars(select(WatchlistItem).order_by(desc(WatchlistItem.score)).limit(5)).all()
        positions = self.db.scalars(select(Position).where(Position.status == "OPEN")).all()
        lines = ["🌅 <b>MORNING MARKET BRIEF</b>",
            f"Gece haberleri: <b>{len(news)}</b> • Açık pozisyon: <b>{len(positions)}</b>"]
        lines += [f"• {item.symbol or 'BIST'} | {item.source} | önem {item.ai_importance or 0} | {item.title[:100]}" for item in news[:5]]
        if trends: lines.append("RS liderleri: " + ", ".join(f"{item.symbol} ({item.relative_strength_1d or 0})" for item in trends))
        if watch: lines.append("Watchlist: " + ", ".join(f"{item.symbol}/{item.score}" for item in watch))
        result = self.notifier.send("\n".join(lines))
        self.db.add(DecisionLog(category="MORNING_BRIEF", decision=result.get("status", "READY"), reason=key,
            details={"news_count": len(news), "watchlist_count": len(watch), "positions": len(positions),
                "execution_authority": False}))
        self.db.commit()
        return {"status": result.get("status", "READY"), "news_count": len(news), "execution_authority": False}
