from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from types import SimpleNamespace

from sqlalchemy import desc, func, or_, select

from app.analysis.pipeline import analyze_frames
from app.market_data.provider import CandleData
from app.market_data.market_session import BistMarketSession
from app.models import Analysis, Candle, MarketStateSnapshot, NewsItem, NewsMarketReaction


def _decimal(value):
    return None if value is None else Decimal(str(value))


def _pct(after, before):
    return (after / before - 1) * Decimal(100) if after is not None and before not in (None, 0) else None


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def candle_quality(candles) -> dict:
    duplicates = len(candles) - len({item.timestamp for item in candles})
    invalid = sum(item.open <= 0 or item.high <= 0 or item.low <= 0 or item.close <= 0 or
                  item.high < max(item.open, item.close) or item.low > min(item.open, item.close) for item in candles)
    missing_timestamp = sum(item.timestamp is None for item in candles)
    ordered = sorted((_utc(item.timestamp) for item in candles if item.timestamp is not None))
    deltas = [(right-left).total_seconds() for left, right in zip(ordered, ordered[1:]) if right > left]
    typical = median(deltas) if deltas else None
    gaps = sum(delta > typical * 1.5 for delta in deltas) if typical else 0
    stale = bool(ordered and datetime.now(timezone.utc) - ordered[-1] > timedelta(days=7))
    status = "INVALID" if invalid or missing_timestamp else "PARTIAL" if duplicates or gaps or stale or len(candles) < 35 else "VALID"
    return {"status": status, "duplicates": duplicates, "invalid_ohlc": invalid, "missing_timestamp": missing_timestamp,
            "gaps": gaps, "stale": stale, "rows": len(candles)}


class MarketMemoryService:
    def __init__(self, db, config):
        self.db, self.config = db, config

    @staticmethod
    def payload_from_analysis(analysis: Analysis, status: str = "RECORDED", xu100_price=None) -> dict:
        details = analysis.details or {}
        indicators = details.get("indicators") or {}
        macd = indicators.get("macd") or {}
        bands = indicators.get("bollinger") or {}
        structure = details.get("structure") or {}
        levels = details.get("levels") or {}
        relative = details.get("relative_strength") or {}
        return {
            "symbol": analysis.symbol, "timeframe": "15m", "timestamp": analysis.signal_candle_time or analysis.analyzed_at,
            "status": status, "price": analysis.price, "ema20": _decimal(indicators.get("ema20")),
            "ema50": _decimal(indicators.get("ema50")), "ema200": _decimal(indicators.get("ema200")),
            "rsi": _decimal(indicators.get("rsi")), "macd": _decimal(macd.get("macd")),
            "macd_signal": _decimal(macd.get("signal")), "macd_histogram": _decimal(macd.get("histogram")),
            "bb_upper": _decimal(bands.get("upper")), "bb_middle": _decimal(bands.get("middle")),
            "bb_lower": _decimal(bands.get("lower")), "atr": _decimal(indicators.get("atr")),
            "atr_pct": _decimal(indicators.get("atr_pct")), "vwap": _decimal(indicators.get("vwap")),
            "rvol": _decimal(indicators.get("rvol")), "volume_sma20": _decimal(indicators.get("volume_sma20")),
            "trend": analysis.trend, "market_structure": analysis.market_structure,
            "swing_high": _decimal((structure.get("last_swing_high") or {}).get("price")),
            "swing_low": _decimal((structure.get("last_swing_low") or {}).get("price")),
            "support": _decimal(levels.get("support")), "resistance": _decimal(levels.get("resistance")),
            "bos": structure.get("bos"), "choch": structure.get("choch"), "setup": analysis.setup,
            "setup_quality": details.get("setup_quality"), "technical_score": analysis.score,
            "risk_reward": _decimal(details.get("risk_reward")),
            "relative_strength_1d": _decimal(relative.get("relative_strength_1d")),
            "relative_strength_5d": _decimal(relative.get("relative_strength_5d")),
            "relative_strength_20d": _decimal(relative.get("relative_strength_20d")),
            "xu100_price": _decimal(xu100_price), "data_source": analysis.data_source,
            "data_quality": "VALID" if analysis.data_valid else "PARTIAL",
        }

    def record_analysis(self, analysis: Analysis, xu100_price=None):
        payload = self.payload_from_analysis(analysis, "RECORDED", xu100_price)
        exists = self.db.scalar(select(MarketStateSnapshot.id).where(
            MarketStateSnapshot.symbol == analysis.symbol,
            MarketStateSnapshot.timeframe == "15m",
            MarketStateSnapshot.timestamp == payload["timestamp"],
        ))
        if exists:
            return None
        snapshot = MarketStateSnapshot(**payload)
        self.db.add(snapshot)
        return snapshot

    def history(self, symbol: str, start=None, end=None, limit=500):
        stmt = select(MarketStateSnapshot).where(MarketStateSnapshot.symbol == symbol.upper())
        if start: stmt = stmt.where(MarketStateSnapshot.timestamp >= start)
        if end: stmt = stmt.where(MarketStateSnapshot.timestamp <= end)
        return self.db.scalars(stmt.order_by(MarketStateSnapshot.timestamp).limit(limit)).all()

    def _frames(self, symbol: str, at: datetime) -> dict[str, list[Candle]]:
        frames = {}
        for timeframe in ("1d", "1h", "15m"):
            rows = self.db.scalars(select(Candle).where(Candle.symbol == symbol.upper(), Candle.timeframe == timeframe,
                Candle.timestamp <= at).order_by(desc(Candle.timestamp)).limit(220)).all()
            frames[timeframe] = [CandleData(_utc(row.timestamp), row.open, row.high, row.low, row.close, row.volume)
                for row in reversed(rows)]
        return frames

    def snapshot_at(self, symbol: str, at: datetime):
        at = _utc(at)
        row = self.db.scalar(select(MarketStateSnapshot).where(MarketStateSnapshot.symbol == symbol.upper(),
            MarketStateSnapshot.timestamp <= at).order_by(desc(MarketStateSnapshot.timestamp)).limit(1))
        if row and at - _utc(row.timestamp) <= timedelta(minutes=15):
            return row
        frames = self._frames(symbol, at)
        if any(len(rows) < 60 for rows in frames.values()):
            return {"symbol": symbol.upper(), "timestamp": at, "status": "NOT_AVAILABLE", "reason": "Yeterli historical candle yok"}
        result = analyze_frames(symbol.upper(), frames, self.config, "replay", at, False)
        transient = SimpleNamespace(symbol=result.symbol, signal_candle_time=result.signal_candle_time, analyzed_at=at,
            price=result.price, details=result.details, trend=result.trend, market_structure=result.market_structure,
            setup=result.setup, score=result.score, data_source="replay", data_valid=result.funnel["data_valid"])
        return self.payload_from_analysis(transient, "RECONSTRUCTED")

    def trend(self, symbol: str, start=None, end=None, limit=1000):
        return [{"timestamp": row.timestamp, "price": row.price, "trend": row.trend,
                 "structure": row.market_structure, "score": row.technical_score, "bos": row.bos, "choch": row.choch}
                for row in self.history(symbol, start, end, limit)]

    def news(self, symbol: str, start=None, end=None, limit=500):
        stmt = select(NewsItem).where(NewsItem.symbol == symbol.upper())
        if start: stmt = stmt.where(NewsItem.published_at >= start)
        if end: stmt = stmt.where(NewsItem.published_at <= end)
        return self.db.scalars(stmt.order_by(NewsItem.published_at).limit(limit)).all()

    def opening_context(self, symbol: str, at=None):
        at = _utc(at or datetime.now(timezone.utc))
        session = BistMarketSession.from_config(self.config)
        local = at.astimezone(session.tz)
        previous = session.previous_trading_day(local.date())
        since = datetime.combine(previous, session.close_time, session.tz).astimezone(timezone.utc)
        rows = self.db.scalars(select(NewsItem).where(NewsItem.symbol == symbol.upper(),
            NewsItem.published_at >= since, NewsItem.published_at <= at, NewsItem.overnight_news.is_(True))).all()
        snapshot = self.db.scalar(select(MarketStateSnapshot).where(MarketStateSnapshot.symbol == symbol.upper(),
            MarketStateSnapshot.timestamp <= at).order_by(desc(MarketStateSnapshot.timestamp)).limit(1))
        previous_close = self.db.scalar(select(Candle.close).where(Candle.symbol == symbol.upper(), Candle.timeframe == "1d",
            Candle.timestamp <= since).order_by(desc(Candle.timestamp)).limit(1))
        sentiments = [item.ai_sentiment for item in rows if item.ai_sentiment]
        sentiment = max(set(sentiments), key=sentiments.count) if sentiments else "NO_DATA"
        return {"symbol": symbol.upper(), "at": at, "overnight_news": len(rows), "sentiment": sentiment,
            "highest_importance": max((item.ai_importance or 0 for item in rows), default=None),
            "previous_close": previous_close, "technical_state": snapshot.trend if snapshot else "NOT_AVAILABLE",
            "relative_strength_1d": snapshot.relative_strength_1d if snapshot else None,
            "execution_authority": False}

    def evaluate_reactions(self, limit=50):
        news_rows = self.db.scalars(select(NewsItem).outerjoin(
            NewsMarketReaction,
            (NewsMarketReaction.news_id == NewsItem.id) & (NewsMarketReaction.symbol == NewsItem.symbol),
        ).where(NewsItem.symbol.is_not(None), or_(
            NewsMarketReaction.id.is_(None), NewsMarketReaction.status != "COMPLETE"
        )).order_by(NewsItem.published_at).limit(limit)).all()
        updated = 0
        for news in news_rows:
            reaction = self.db.scalar(select(NewsMarketReaction).where(NewsMarketReaction.news_id == news.id,
                NewsMarketReaction.symbol == news.symbol)) or NewsMarketReaction(news_id=news.id, symbol=news.symbol)
            intraday = self.db.scalars(select(Candle).where(Candle.symbol == news.symbol, Candle.timeframe == "15m")
                .order_by(Candle.timestamp)).all()
            daily = self.db.scalars(select(Candle).where(Candle.symbol == news.symbol, Candle.timeframe == "1d")
                .order_by(Candle.timestamp)).all()
            published_at = _utc(news.published_at)
            session = BistMarketSession.from_config(self.config)
            local_news = published_at.astimezone(session.tz)
            before = [row for row in intraday if _utc(row.timestamp) <= published_at]
            after = [row for row in intraday if _utc(row.timestamp) > published_at]
            daily_before = [row for row in daily if _utc(row.timestamp).astimezone(session.tz).date() < local_news.date()
                or (_utc(row.timestamp).astimezone(session.tz).date() == local_news.date() and local_news.time() >= session.close_time)]
            daily_after = [row for row in daily if _utc(row.timestamp).astimezone(session.tz).date() > local_news.date()]
            reaction.price_before = before[-1].close if before else None
            reaction.previous_close = daily_before[-1].close if daily_before else reaction.price_before
            reaction.next_open = after[0].open if after else None
            reaction.next_close = after[0].close if after else None
            reaction.gap_pct = _pct(reaction.next_open, reaction.previous_close)
            reaction.return_15m = _pct(after[0].close, reaction.price_before) if after else None
            reaction.return_1h = _pct(after[3].close, reaction.price_before) if len(after) >= 4 else None
            reaction.return_1d = _pct(daily_after[0].close, reaction.previous_close) if daily_after else None
            reaction.return_5d = _pct(daily_after[4].close, reaction.previous_close) if len(daily_after) >= 5 else None
            prior_volume = sum((row.volume for row in before[-20:]), Decimal(0)) / len(before[-20:]) if before else None
            reaction.volume_change = _pct(after[0].volume, prior_volume) if after and prior_volume else None
            reaction.rvol_after = after[0].volume / prior_volume if after and prior_volume else None
            xu = self.db.scalars(select(Candle).where(Candle.symbol == "XU100", Candle.timeframe == "1d")
                .order_by(Candle.timestamp)).all()
            xu_before = [row for row in xu if _utc(row.timestamp) <= published_at]
            xu_after = [row for row in xu if _utc(row.timestamp) > published_at]
            reaction.xu100_return_1d = _pct(xu_after[0].close, xu_before[-1].close) if xu_before and xu_after else None
            reaction.abnormal_return_1d = (reaction.return_1d - reaction.xu100_return_1d
                if reaction.return_1d is not None and reaction.xu100_return_1d is not None else None)
            reaction.status = "COMPLETE" if reaction.return_5d is not None else "PARTIAL" if any(
                value is not None for value in (reaction.return_15m, reaction.return_1h, reaction.return_1d)) else "PENDING"
            reaction.evaluated_at = datetime.now(timezone.utc)
            self.db.add(reaction); updated += 1
        self.db.commit()
        return {"status": "OK", "evaluated": updated}

    def reactions(self, symbol: str, limit=500):
        stmt = select(NewsMarketReaction).where(NewsMarketReaction.symbol == symbol.upper()).order_by(desc(NewsMarketReaction.evaluated_at)).limit(limit)
        return self.db.scalars(stmt).all()

    def event_study(self):
        rows = self.db.execute(select(NewsItem.category, NewsItem.ai_sentiment, NewsMarketReaction.return_1d,
            NewsMarketReaction.return_5d, NewsMarketReaction.abnormal_return_1d, NewsMarketReaction.volume_change)
            .join(NewsMarketReaction, NewsMarketReaction.news_id == NewsItem.id)).all()
        groups = {}
        for category, sentiment, r1, r5, abnormal, volume in rows:
            group = groups.setdefault(category, {"news_count": 0, "positive_ai_count": 0, "negative_ai_count": 0,
                "return_1d": [], "return_5d": [], "abnormal": [], "volume": []})
            group["news_count"] += 1; group["positive_ai_count"] += int(sentiment == "POSITIVE"); group["negative_ai_count"] += int(sentiment == "NEGATIVE")
            if r1 is not None: group["return_1d"].append(float(r1))
            if r5 is not None: group["return_5d"].append(float(r5))
            if abnormal is not None: group["abnormal"].append(float(abnormal))
            if volume is not None: group["volume"].append(float(volume))
        result = []
        for category, group in sorted(groups.items()):
            r1 = group.pop("return_1d"); r5 = group.pop("return_5d"); abnormal = group.pop("abnormal"); volume = group.pop("volume")
            result.append({"category": category, **group, "avg_return_1d": sum(r1)/len(r1) if r1 else None,
                "median_return_1d": median(r1) if r1 else None, "avg_return_5d": sum(r5)/len(r5) if r5 else None,
                "positive_rate": sum(value > 0 for value in r1)/len(r1)*100 if r1 else None,
                "avg_abnormal_return": sum(abnormal)/len(abnormal) if abnormal else None,
                "avg_volume_change": sum(volume)/len(volume) if volume else None})
        return result

    def health(self):
        snapshot_count = self.db.scalar(select(func.count()).select_from(MarketStateSnapshot)) or 0
        candle_count = self.db.scalar(select(func.count()).select_from(Candle)) or 0
        return {"status": "OK" if snapshot_count else "NO_DATA", "price_history": "OK" if candle_count else "NO_DATA",
            "snapshot_count": snapshot_count, "reaction_count": self.db.scalar(select(func.count()).select_from(NewsMarketReaction)) or 0,
            "news_count": self.db.scalar(select(func.count()).select_from(NewsItem)) or 0,
            "candle_count": candle_count,
            "last_snapshot": self.db.scalar(select(func.max(MarketStateSnapshot.timestamp))),
            "last_news": self.db.scalar(select(func.max(NewsItem.published_at)))}
