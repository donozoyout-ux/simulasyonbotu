from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config.settings import AppSettings
from app.db.session import Base
from app.market_data.provider import CandleData
from app.market_memory.backfill import BackfillService
from app.market_memory.service import MarketMemoryService, candle_quality
from app.models import Analysis, BackfillState, Candle, MarketStateSnapshot, NewsItem, NewsMarketReaction
from app.news.models import NewsRecord
from app.news.service import NewsService
import app.news.service as news_module


def config(**updates):
    values = {"news_enabled": True, "kap_enabled": True, "ai_enabled": False,
              "backfill_symbols_per_cycle": 1, "backfill_candle_limit": 100}
    values.update(updates)
    return AppSettings(_env_file=None, **values)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


class Source:
    name = "KAP"
    def __init__(self, rows): self.rows = rows
    def fetch(self): return self.rows


class Analyzer:
    def evaluate(self, _):
        return {"status": "OK", "model": "test", "sentiment": "POSITIVE", "importance": 90,
                "summary": "özet", "horizon": "SHORT_TERM", "risks": [], "tags": ["kap"]}


class Notifier:
    def send(self, _): return True


def add_news(db, published, symbol="ASELS", category="NEW_CONTRACT"):
    item = NewsItem(symbol=symbol, source="KAP", source_id=f"n-{published.timestamp()}", title="haber",
        content="içerik", url="https://www.kap.org.tr/x", category=category, published_at=published,
        content_hash=f"h-{published.timestamp()}", ai_status="OK", ai_sentiment="POSITIVE", ai_importance=90)
    db.add(item); db.commit(); return item


def add_candle(db, symbol, timeframe, timestamp, close=100, volume=1000):
    value = Decimal(str(close))
    row = Candle(symbol=symbol, timeframe=timeframe, timestamp=timestamp, open=value, high=value+1,
        low=value-1, close=value, volume=Decimal(str(volume)), source="test")
    db.add(row); return row


def test_news_archive_persists_lifecycle_and_overnight(db):
    news_module._LAST_REFRESH_AT = 0
    published = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)  # Saturday
    record = NewsRecord("KAP", "42", "ASELS sözleşme", "metin", "https://www.kap.org.tr/x", published, "ASELS")
    result = NewsService(db, config(), [Source([record])], Analyzer(), Notifier()).refresh()
    item = db.scalar(select(NewsItem))
    assert result["new_items"] == 1 and item.first_seen_at and item.updated_at
    assert item.overnight_news is True and item.telegram_sent_at is not None


def test_news_archive_deduplicates_across_restart(db):
    published = datetime(2026, 9, 12, tzinfo=timezone.utc)
    record = NewsRecord("KAP", "42", "ASELS", "x", "https://www.kap.org.tr/x", published, "ASELS")
    service = NewsService(db, config(), [Source([record])], Analyzer(), Notifier())
    news_module._LAST_REFRESH_AT = 0; service.refresh()
    news_module._LAST_REFRESH_AT = 0; service.refresh()
    assert db.scalar(select(func.count()).select_from(NewsItem)) == 1


def test_archive_filters_symbol_overnight_and_reaction(db):
    item = add_news(db, datetime(2026, 9, 12, tzinfo=timezone.utc))
    item.overnight_news = True
    db.add(NewsMarketReaction(news_id=item.id, symbol="ASELS", status="PARTIAL")); db.commit()
    rows = NewsService(db, config(), [], Analyzer(), Notifier()).archive(
        symbol="ASELS", min_importance=80, overnight_only=True, reaction_only=True)
    assert len(rows) == 1 and rows[0]["reaction"]["status"] == "PARTIAL"


def test_news_health_includes_24h_counters(db):
    item = add_news(db, datetime.now(timezone.utc)); item.overnight_news = True; db.commit()
    health = NewsService(db, config(), [], Analyzer(), Notifier()).health()
    assert health["last_24h"] == health["important_24h"] == health["overnight_24h"] == health["kap_24h"] == 1


def test_kap_nonstandard_waf_status_is_reported_without_bypass(db):
    class WafSource:
        name = "KAP"
        def fetch(self):
            response = httpx.Response(666, request=httpx.Request("GET", "https://www.kap.org.tr"))
            raise httpx.HTTPStatusError("blocked", request=response.request, response=response)
    news_module._LAST_REFRESH_AT = 0
    result = NewsService(db, config(), [WafSource()], Analyzer(), Notifier()).refresh()
    assert result["status"] == "PARTIAL"
    assert NewsService(db, config(), [], Analyzer(), Notifier()).health()["sources"]["KAP"]["status"] == "WAF_BLOCKED"


def analysis(at):
    return Analysis(symbol="ASELS", analyzed_at=at, signal_candle_time=at, price=Decimal("100"), score=82,
        trend="bullish", market_structure="BULLISH", setup="BREAKOUT", decision="POSSIBLE_ENTRY", reason="test",
        data_source="hybrid", data_valid=True, details={"indicators": {"ema20": 98, "rsi": 61,
        "macd": {"macd": 1, "signal": .5, "histogram": .5}, "bollinger": {"upper": 105, "middle": 99, "lower": 93},
        "atr": 2, "rvol": 1.4}, "structure": {}, "levels": {"support": 95, "resistance": 101},
        "relative_strength": {"relative_strength_1d": 1.2}, "setup_quality": 85})


def test_snapshot_persistence_and_duplicate_guard(db):
    service = MarketMemoryService(db, config()); row = analysis(datetime.now(timezone.utc))
    db.add(row); service.record_analysis(row, Decimal("12000")); db.commit()
    assert service.record_analysis(row) is None
    saved = db.scalar(select(MarketStateSnapshot))
    assert saved.technical_score == 82 and saved.rsi == 61 and saved.xu100_price == 12000


def test_history_and_trend_are_chronological(db):
    service = MarketMemoryService(db, config()); now = datetime.now(timezone.utc)
    for offset in (2, 1, 0):
        row = analysis(now - timedelta(hours=offset)); db.add(row); service.record_analysis(row)
    db.commit(); trend = service.trend("asels")
    assert len(trend) == 3 and trend[0]["timestamp"] < trend[-1]["timestamp"]


def test_snapshot_not_available_without_historical_candles(db):
    result = MarketMemoryService(db, config()).snapshot_at("ASELS", datetime.now(timezone.utc))
    assert result["status"] == "NOT_AVAILABLE"


def test_news_reaction_calculates_next_open_and_returns(db):
    base = datetime(2026, 9, 8, 9, tzinfo=timezone.utc); item = add_news(db, base + timedelta(minutes=10))
    add_candle(db, "ASELS", "15m", base, 100)
    for index in range(1, 6): add_candle(db, "ASELS", "15m", base + timedelta(minutes=15*index), 100+index, 1100+index*20)
    add_candle(db, "ASELS", "1d", base-timedelta(days=1), 100)
    add_candle(db, "XU100", "1d", base-timedelta(days=1), 10000)
    for index in range(1, 7):
        add_candle(db, "ASELS", "1d", base+timedelta(days=index), 100+index)
        add_candle(db, "XU100", "1d", base+timedelta(days=index), 10000+index*10)
    db.commit(); MarketMemoryService(db, config()).evaluate_reactions()
    reaction = db.scalar(select(NewsMarketReaction).where(NewsMarketReaction.news_id == item.id))
    assert reaction.next_open == 101 and reaction.return_15m == 1 and reaction.return_5d == 5
    assert reaction.abnormal_return_1d is not None and reaction.status == "COMPLETE"


def test_event_study_groups_category(db):
    item = add_news(db, datetime.now(timezone.utc), category="DIVIDEND")
    db.add(NewsMarketReaction(news_id=item.id, symbol="ASELS", return_1d=Decimal("2"),
        return_5d=Decimal("4"), abnormal_return_1d=Decimal("1"), volume_change=Decimal("20"), status="COMPLETE")); db.commit()
    result = MarketMemoryService(db, config()).event_study()[0]
    assert result["category"] == "DIVIDEND" and result["positive_rate"] == 100


class CandleProvider:
    def get_candles(self, symbol, timeframe, limit):
        return [CandleData(datetime(2026, 9, 1, tzinfo=timezone.utc), Decimal("10"), Decimal("11"),
            Decimal("9"), Decimal("10"), Decimal("100"))]


def test_backfill_is_idempotent_and_cursor_resumes(db):
    service = BackfillService(db, config(), CandleProvider(), ["ASELS", "THYAO"])
    first = service.run(); second = service.run(); third = service.run()
    assert first["added_candles"] == 4 and second["added_candles"] == 4 and third["added_candles"] == 0
    assert db.get(BackfillState, service.task).processed_items == 2 and third["status"] == "COMPLETE"


def test_retention_is_non_destructive_by_default(db):
    add_candle(db, "ASELS", "5m", datetime(2020, 1, 1, tzinfo=timezone.utc)); db.commit()
    result = BackfillService(db, config(), CandleProvider(), ["ASELS"]).maintain_retention()
    assert result == {"status": "DISABLED", "deleted": 0} and db.scalar(select(func.count()).select_from(Candle)) == 1


@pytest.mark.parametrize("rows,expected", [([], "PARTIAL"),
    ([CandleData(datetime.now(timezone.utc), Decimal("1"), Decimal("2"), Decimal("1"), Decimal("1"), Decimal("1"))], "PARTIAL")])
def test_candle_quality_marks_short_history_partial(rows, expected):
    assert candle_quality(rows)["status"] == expected


def test_candle_quality_ignores_expected_overnight_and_weekend_gaps():
    friday = datetime(2026, 9, 11, 14, 45, tzinfo=timezone.utc)
    monday = datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc)
    intraday = [CandleData(friday, Decimal("1"), Decimal("2"), Decimal("1"), Decimal("1"), Decimal("1")),
        CandleData(monday, Decimal("1"), Decimal("2"), Decimal("1"), Decimal("1"), Decimal("1"))]
    assert candle_quality(intraday, "15m")["gaps"] == 0
    assert candle_quality(intraday, "1d")["gaps"] == 0


def test_historical_snapshot_is_deterministically_reconstructed(db):
    now = datetime(2026, 9, 11, 14, tzinfo=timezone.utc)
    frames = {"1d": timedelta(days=1), "1h": timedelta(hours=1), "15m": timedelta(minutes=15)}
    for timeframe, step in frames.items():
        for index in range(90):
            value = 80 + index * .25
            add_candle(db, "ASELS", timeframe, now - step * (89-index), value, 1000+index*10)
    db.commit()
    result = MarketMemoryService(db, config()).snapshot_at("ASELS", now)
    assert result["status"] == "RECONSTRUCTED" and result["technical_score"] is not None
    assert result["rsi"] is not None and result["data_source"] == "replay"


def test_history_and_archive_http_apis(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import router
    from app.db.session import get_db
    now = datetime.now(timezone.utc); row = analysis(now); db.add(row)
    MarketMemoryService(db, config()).record_analysis(row); add_news(db, now); db.commit()
    app = FastAPI(); app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        history = client.get("/api/market-history/ASELS")
        archive = client.get("/api/news/archive?symbol=ASELS&min_importance=80")
    assert history.status_code == 200 and len(history.json()) == 1
    assert archive.status_code == 200 and len(archive.json()) == 1


def test_market_closed_embedded_cycle_runs_news_without_orders(monkeypatch, db):
    import app.services.embedded_worker as module
    calls = {"news": 0, "orders": 0}
    class Context:
        def __enter__(self): return db
        def __exit__(self, *_): return False
    class ClosedSession:
        def is_open(self, *_): return False
    class Forward:
        def __init__(self, *_): pass
        def run_once(self): return {"status": "market_closed", "orders": 0}
    class News:
        def __init__(self, *_): pass
        def refresh(self): calls["news"] += 1; return {"status": "OK"}
    class Backfill:
        def __init__(self, *_): pass
        def run(self): return {"status": "RUNNING"}
        def maintain_retention(self): return {"status": "DISABLED", "deleted": 0}
    class Memory:
        def __init__(self, *_): pass
        def evaluate_reactions(self, _): return {"status": "OK"}
    monkeypatch.setattr(module, "SessionLocal", lambda: Context())
    monkeypatch.setattr(module.BistMarketSession, "from_config", lambda *_: ClosedSession())
    monkeypatch.setattr(module, "ForwardWorker", Forward); monkeypatch.setattr(module, "NewsService", News)
    monkeypatch.setattr(module, "BackfillService", Backfill); monkeypatch.setattr(module, "MarketMemoryService", Memory)
    result = module.EmbeddedWorker(config()).run_cycle()
    assert result["status"] == "market_closed" and result["orders"] == 0 and calls["news"] == 1


def test_opening_context_uses_only_persisted_overnight_observations(db):
    at = datetime(2026, 9, 14, 6, 30, tzinfo=timezone.utc)
    item = add_news(db, datetime(2026, 9, 13, 20, tzinfo=timezone.utc)); item.overnight_news = True
    add_candle(db, "ASELS", "1d", datetime(2026, 9, 11, tzinfo=timezone.utc), 123)
    db.commit(); context = MarketMemoryService(db, config()).opening_context("ASELS", at)
    assert context["overnight_news"] == 1 and context["highest_importance"] == 90
    assert context["previous_close"] == 123 and context["execution_authority"] is False


def test_morning_brief_is_sent_once_and_has_no_execution_authority(db):
    from app.market_memory.brief import MorningBriefService
    class BriefNotifier:
        def __init__(self): self.calls = 0
        def send(self, text): self.calls += 1; assert "MORNING MARKET BRIEF" in text; return {"status": "SENT"}
    at = datetime(2026, 9, 14, 6, 30, tzinfo=timezone.utc)
    item = add_news(db, datetime(2026, 9, 13, 20, tzinfo=timezone.utc)); item.overnight_news = True; db.commit()
    notifier = BriefNotifier(); service = MorningBriefService(db, config(), notifier)
    assert service.run(at)["execution_authority"] is False
    assert service.run(at)["status"] == "DEDUPED" and notifier.calls == 1
