from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from types import SimpleNamespace

from sqlalchemy import case, desc, func, or_, select

from app.analysis.pipeline import analyze_frames
from app.market_data.provider import CandleData
from app.market_data.market_session import BistMarketSession
from app.models import Analysis, Candle, MarketStateSnapshot, NewsItem, NewsMarketReaction
from app.services.collection_activity import log_activity
from app.services.historical_candles import BoundedTTLCache


_aggregate_cache = BoundedTTLCache(max_entries=32)


def aggregate_cache_stats() -> dict:
    return _aggregate_cache.stats()


def _decimal(value):
    return None if value is None else Decimal(str(value))


def _pct(after, before):
    return (after / before - 1) * Decimal(100) if after is not None and before not in (None, 0) else None


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def candle_quality(candles, timeframe=None) -> dict:
    duplicates = len(candles) - len({item.timestamp for item in candles})
    invalid = sum(item.open <= 0 or item.high <= 0 or item.low <= 0 or item.close <= 0 or
                  item.high < max(item.open, item.close) or item.low > min(item.open, item.close) for item in candles)
    missing_timestamp = sum(item.timestamp is None for item in candles)
    ordered = sorted((_utc(item.timestamp) for item in candles if item.timestamp is not None))
    pairs = [(left, right) for left, right in zip(ordered, ordered[1:]) if right > left]
    if timeframe in {"5m", "15m", "1h"}:
        session = BistMarketSession()
        pairs = [(left, right) for left, right in pairs
            if left.astimezone(session.tz).date() == right.astimezone(session.tz).date()]
    deltas = [(right-left).total_seconds() for left, right in pairs]
    typical = median(deltas) if deltas else None
    if timeframe == "1d":
        gaps = sum((right.date()-left.date()).days > 4 for left, right in pairs)
    else:
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
            "analysis_mode": details.get("analysis_mode", "LIVE"),
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
        log_activity(self.db,"MARKET_MEMORY",analysis.symbol,"SNAPSHOT","RECORDED","15m snapshot")
        return snapshot

    def history(self, symbol: str, start=None, end=None, limit=500):
        stmt = select(MarketStateSnapshot).where(MarketStateSnapshot.symbol == symbol.upper())
        if start: stmt = stmt.where(MarketStateSnapshot.timestamp >= start)
        if end: stmt = stmt.where(MarketStateSnapshot.timestamp <= end)
        return list(reversed(self.db.scalars(
            stmt.order_by(desc(MarketStateSnapshot.timestamp)).limit(limit)).all()))

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
        stmt = select(MarketStateSnapshot.timestamp, MarketStateSnapshot.price, MarketStateSnapshot.trend,
            MarketStateSnapshot.market_structure, MarketStateSnapshot.technical_score,
            MarketStateSnapshot.bos, MarketStateSnapshot.choch, MarketStateSnapshot.analysis_mode
        ).where(MarketStateSnapshot.symbol == symbol.upper())
        if start: stmt = stmt.where(MarketStateSnapshot.timestamp >= start)
        if end: stmt = stmt.where(MarketStateSnapshot.timestamp <= end)
        rows = list(reversed(self.db.execute(
            stmt.order_by(desc(MarketStateSnapshot.timestamp)).limit(limit)).all()))
        return [{"timestamp": row.timestamp, "price": row.price, "trend": row.trend,
                 "structure": row.market_structure, "score": row.technical_score, "bos": row.bos,
                 "choch": row.choch, "analysis_mode": row.analysis_mode} for row in rows]

    def news(self, symbol: str, start=None, end=None, limit=500):
        stmt = select(NewsItem).where(NewsItem.symbol == symbol.upper())
        if start: stmt = stmt.where(NewsItem.published_at >= start)
        if end: stmt = stmt.where(NewsItem.published_at <= end)
        return list(reversed(self.db.scalars(stmt.order_by(desc(NewsItem.published_at)).limit(limit)).all()))

    def memory_symbols(self):
        """Small aggregate inventory used by the UI filters; no provider access."""
        cache_key = ("memory_symbols", id(self.db.get_bind()))
        cached = _aggregate_cache.get(cache_key)
        if cached is not None:
            return cached
        inventory = {}
        for symbol, count, latest in self.db.execute(select(
            MarketStateSnapshot.symbol, func.count(MarketStateSnapshot.id), func.max(MarketStateSnapshot.timestamp)
        ).group_by(MarketStateSnapshot.symbol)):
            inventory[symbol] = {"symbol": symbol, "snapshot_count": count, "news_count": 0,
                "reaction_count": 0, "completed_reaction_count": 0, "latest_snapshot_at": latest,
                "latest_news_at": None}
        for symbol, count, latest in self.db.execute(select(
            NewsItem.symbol, func.count(NewsItem.id), func.max(NewsItem.published_at)
        ).where(NewsItem.symbol.is_not(None)).group_by(NewsItem.symbol)):
            row = inventory.setdefault(symbol, {"symbol": symbol, "snapshot_count": 0, "news_count": 0,
                "reaction_count": 0, "completed_reaction_count": 0, "latest_snapshot_at": None,
                "latest_news_at": None})
            row.update(news_count=count, latest_news_at=latest)
        for symbol, count, complete in self.db.execute(select(
            NewsMarketReaction.symbol, func.count(NewsMarketReaction.id),
            func.sum(case((NewsMarketReaction.status == "COMPLETE", 1), else_=0))
        ).group_by(NewsMarketReaction.symbol)):
            row = inventory.setdefault(symbol, {"symbol": symbol, "snapshot_count": 0, "news_count": 0,
                "reaction_count": 0, "completed_reaction_count": 0, "latest_snapshot_at": None,
                "latest_news_at": None})
            row.update(reaction_count=count, completed_reaction_count=complete or 0)
        result = sorted(inventory.values(), key=lambda row: (
            -row["news_count"], -row["snapshot_count"], row["symbol"]))
        _aggregate_cache.put(cache_key, result, 30)
        return result

    def linked_news_symbols(self):
        rows = [row for row in self.memory_symbols() if row["news_count"]]
        return sorted(rows, key=lambda row: (
            -row["news_count"], -(_utc(row["latest_news_at"]).timestamp() if row["latest_news_at"] else 0)
        ))

    @staticmethod
    def _future_return(row, price):
        return _pct(row.close, price) if row is not None else None

    def _future_performance(self, symbol: str, at: datetime, price):
        price = _decimal(price)
        intraday = self.db.scalars(select(Candle).where(
            Candle.symbol == symbol, Candle.timeframe == "15m", Candle.timestamp > at,
            Candle.timestamp <= at + timedelta(days=10)
        ).order_by(Candle.timestamp).limit(600)).all()
        daily = self.db.scalars(select(Candle).where(
            Candle.symbol == symbol, Candle.timeframe == "1d", Candle.timestamp > at
        ).order_by(Candle.timestamp).limit(5)).all()
        one_day = daily[0] if daily else None
        five_day = daily[4] if len(daily) >= 5 else None
        excursion_rows = []
        if five_day:
            excursion_end = _utc(five_day.timestamp) + timedelta(days=1)
            excursion_rows = [row for row in intraday if _utc(row.timestamp) <= excursion_end]
        values = {
            "return_15m": self._future_return(intraday[0] if intraday else None, price),
            "return_1h": self._future_return(intraday[3] if len(intraday) >= 4 else None, price),
            "return_1d": self._future_return(one_day, price),
            "return_5d": self._future_return(five_day, price),
            "mfe_5d": _pct(max((row.high for row in excursion_rows), default=None), price),
            "mae_5d": _pct(min((row.low for row in excursion_rows), default=None), price),
        }
        available = sum(value is not None for value in values.values())
        values["status"] = "COMPLETE" if available == len(values) else "PARTIAL" if available else "NOT_AVAILABLE"
        return values

    @staticmethod
    def _decision_quality(score, return_1d):
        if return_1d is None:
            return "NOT_ENOUGH_DATA"
        if score is not None and score >= 82 and return_1d > Decimal("1"):
            return "GOOD_FOLLOW_THROUGH"
        if score is not None and score >= 82 and return_1d < Decimal("-1"):
            return "FALSE_POSITIVE"
        return "NEUTRAL"

    def snapshot_detail(self, symbol: str, at: datetime):
        """Inspect persisted history only. This deliberately has no provider or AI dependency."""
        symbol, at = symbol.upper(), _utc(at)
        snapshot = self.snapshot_at(symbol, at)
        recorded = isinstance(snapshot, MarketStateSnapshot)
        snapshot_time = _utc(snapshot.timestamp) if recorded else _utc(snapshot.get("timestamp"))
        if snapshot_time is None or (not recorded and snapshot.get("status") == "NOT_AVAILABLE"):
            return {"snapshot": snapshot, "analysis": None, "news": [], "reactions": [],
                "future_performance": {"return_15m": None, "return_1h": None, "return_1d": None,
                    "return_5d": None, "mfe_5d": None, "mae_5d": None, "status": "NOT_AVAILABLE",
                    "decision_quality": "NOT_ENOUGH_DATA"}, "status": "PARTIAL"}
        analysis = self.db.scalar(select(Analysis).where(
            Analysis.symbol == symbol, Analysis.signal_candle_time == snapshot_time
        ).order_by(desc(Analysis.analyzed_at)).limit(1)) if recorded else None
        nearby = self.db.scalars(select(NewsItem).where(
            NewsItem.symbol == symbol,
            NewsItem.published_at >= snapshot_time - timedelta(hours=24),
            NewsItem.published_at <= snapshot_time + timedelta(hours=24),
        ).order_by(NewsItem.published_at).limit(100)).all()
        news_ids = [item.id for item in nearby]
        reactions = self.db.scalars(select(NewsMarketReaction).where(
            NewsMarketReaction.symbol == symbol, NewsMarketReaction.news_id.in_(news_ids)
        ).order_by(NewsMarketReaction.news_id).limit(100)).all() if news_ids else []
        price = snapshot.price if recorded else snapshot.get("price")
        future = self._future_performance(symbol, snapshot_time, price)
        score = snapshot.technical_score if recorded else snapshot.get("technical_score")
        future["decision_quality"] = self._decision_quality(score, future["return_1d"])
        status = snapshot.status if recorded else snapshot.get("status", "RECONSTRUCTED")
        if recorded and analysis is None:
            status = "PARTIAL"
        return {"snapshot": snapshot, "analysis": analysis, "news": nearby, "reactions": reactions,
            "future_performance": future, "status": status}

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

    def evaluate_reactions(self, limit=20):
        now = datetime.now(timezone.utc)
        # Upgrade legacy linked news into the durable queue without evaluating an unbounded set.
        missing = self.db.scalars(select(NewsItem).outerjoin(NewsMarketReaction,
            (NewsMarketReaction.news_id == NewsItem.id) & (NewsMarketReaction.symbol == NewsItem.symbol))
            .where(NewsItem.symbol.is_not(None), NewsMarketReaction.id.is_(None))
            .order_by(NewsItem.published_at).limit(limit)).all()
        for news in missing:
            self.db.add(NewsMarketReaction(news_id=news.id, symbol=news.symbol, status="PENDING", next_evaluation_at=now))
        if missing: self.db.flush()
        rows = self.db.execute(select(NewsMarketReaction, NewsItem).join(NewsItem, NewsItem.id == NewsMarketReaction.news_id)
            .where(NewsMarketReaction.status.not_in(("COMPLETE", "ERROR")), or_(
                NewsMarketReaction.next_evaluation_at.is_(None), NewsMarketReaction.next_evaluation_at <= now))
            .order_by(NewsMarketReaction.next_evaluation_at, NewsMarketReaction.id).limit(limit)).all()
        updated = 0
        for reaction, news in rows:
          try:
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
            reaction.pre_return_15m = _pct(before[-1].close, before[-2].close) if len(before) >= 2 else None
            if news.overnight_news:
                reaction.first_15m_return = reaction.return_15m
                reaction.first_1h_return = reaction.return_1h
                reaction.eod_return = reaction.return_1d
            if not after:
                reaction.status = "WAITING_MARKET_OPEN" if news.overnight_news else "WAITING_15M"
                delay = timedelta(hours=12) if news.overnight_news else timedelta(minutes=15)
            elif reaction.return_1h is None: reaction.status,delay="WAITING_1H",timedelta(hours=1)
            elif reaction.return_1d is None: reaction.status,delay="WAITING_1D",timedelta(hours=12)
            elif reaction.return_5d is None: reaction.status,delay="WAITING_5D",timedelta(days=1)
            else: reaction.status,delay="COMPLETE",None
            reaction.evaluated_at=reaction.last_evaluation_at=now
            reaction.next_evaluation_at=now+delay if delay else None
            reaction.attempts=(reaction.attempts or 0)+1;reaction.error=None
            self.db.add(reaction);log_activity(self.db,"REACTION",news.symbol,"EVALUATE",reaction.status,news.title[:180]);updated+=1
          except Exception as exc:
            reaction.status="ERROR";reaction.error=f"{type(exc).__name__}: evaluation failed"[:500]
            reaction.last_evaluation_at=now;reaction.attempts=(reaction.attempts or 0)+1
            log_activity(self.db,"REACTION",news.symbol,"EVALUATE","ERROR",reaction.error);updated+=1
        self.db.commit()
        return {"status": "OK", "evaluated": updated}

    def reaction_status(self):
        counts=dict(self.db.execute(select(NewsMarketReaction.status,func.count()).group_by(NewsMarketReaction.status)).all())
        keys=("PENDING","WAITING_MARKET_OPEN","WAITING_15M","WAITING_1H","WAITING_1D","WAITING_5D","COMPLETE","ERROR")
        result={key.lower():counts.get(key,0) for key in keys}; total=sum(result.values())
        result.update({"total":total,"progress_pct":round(result["complete"]*100/total,1) if total else 0.0})
        return result

    def recent_reactions(self,limit=100):
        rows=self.db.execute(select(NewsMarketReaction,NewsItem).join(NewsItem,NewsItem.id==NewsMarketReaction.news_id)
            .order_by(desc(NewsMarketReaction.last_evaluation_at),desc(NewsMarketReaction.id)).limit(limit)).all()
        return [{**{c.name:getattr(reaction,c.name) for c in NewsMarketReaction.__table__.columns},
            "title":news.title,"source":news.source,"published_at":news.published_at} for reaction,news in rows]

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
        cache_key = ("health", id(self.db.get_bind()))
        cached = _aggregate_cache.get(cache_key)
        if cached is not None:
            return dict(cached)
        snapshot_count = self.db.scalar(select(func.count()).select_from(MarketStateSnapshot)) or 0
        candle_count = self.db.scalar(select(func.count()).select_from(Candle)) or 0
        frames=dict(self.db.execute(select(MarketStateSnapshot.timeframe,func.count()).group_by(MarketStateSnapshot.timeframe)).all())
        recorded=self.db.scalar(select(func.count()).select_from(MarketStateSnapshot).where(MarketStateSnapshot.status=="RECORDED")) or 0
        reconstructed=self.db.scalar(select(func.count()).select_from(MarketStateSnapshot).where(MarketStateSnapshot.status=="RECONSTRUCTED")) or 0
        errors=self.db.scalar(select(func.count()).select_from(MarketStateSnapshot).where(MarketStateSnapshot.data_quality=="INVALID")) or 0
        symbols=self.db.scalar(select(func.count(func.distinct(MarketStateSnapshot.symbol)))) or 0
        last=self.db.scalar(select(func.max(MarketStateSnapshot.timestamp)))
        result = {"status": "OK" if snapshot_count else "NO_DATA", "price_history": "OK" if candle_count else "NO_DATA",
            "snapshot_count": snapshot_count, "reaction_count": self.db.scalar(select(func.count()).select_from(NewsMarketReaction)) or 0,
            "news_count": self.db.scalar(select(func.count()).select_from(NewsItem)) or 0,
            "candle_count": candle_count,"symbols":symbols,"recorded_count":recorded,"reconstructed_count":reconstructed,
            "error_count":errors,"timeframes":{key:frames.get(key,0) for key in ("5m","15m","1h","1d")},
            "last_snapshot":last,"last_snapshot_at":last,
            "last_news": self.db.scalar(select(func.max(NewsItem.published_at)))}
        _aggregate_cache.put(cache_key, result, 30)
        return dict(result)
