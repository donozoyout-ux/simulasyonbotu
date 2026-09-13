from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import router
from app.config.settings import AppSettings
from app.db.session import Base, get_db
from app.market_memory.backfill import BackfillService
from app.market_memory.service import MarketMemoryService
from app.models import Candle, DataCollectionActivity, NewsCompanyLink, NewsItem, NewsMarketReaction, NewsSourceState
from app.news.company_aliases import COMPANY_MASTER, CompanyIdentity, map_company_symbol, normalize_company_text, resolve_company_symbols
from app.news.models import NewsRecord
from app.news.service import NewsService
from app.news.sources.registry import SOURCE_REGISTRY
import app.news.service as news_module


def config(**updates):
    values={"news_enabled":True,"kap_enabled":True,"ai_enabled":False,"backfill_symbols_per_cycle":1}
    values.update(updates);return AppSettings(_env_file=None,**values)


def session():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine);return Session(engine,expire_on_commit=False)


class Analyzer:
    calls=0
    def evaluate(self,_):
        self.calls+=1
        return {"status":"OK","model":"test","sentiment":"NEUTRAL","importance":10,"summary":"summary","horizon":"SHORT_TERM","risks":[],"tags":[]}


class Notifier:
    def send(self,_):return False


class Source:
    def __init__(self,name,rows=None,error=False):self.name,self.rows,self.error=name,rows or [],error
    def fetch(self):
        if self.error:raise RuntimeError("unavailable")
        return self.rows


def record(source="AA",source_id="1",title="Türk Hava Yolları büyüyor"):
    return NewsRecord(source,source_id,title,"content",f"https://www.aa.com.tr/tr/{source_id}",datetime.now(timezone.utc))


def add_news(db,at,overnight=False):
    item=NewsItem(symbol="THYAO",source="AA",source_id=str(at.timestamp()),title="THYAO haber",content="x",
        url="https://www.aa.com.tr/tr/x",category="OTHER",published_at=at,content_hash=str(at.timestamp()),overnight_news=overnight)
    db.add(item);db.commit();return item


def add_candle(db,timeframe,at,close):
    value=Decimal(str(close));db.add(Candle(symbol="THYAO",timeframe=timeframe,timestamp=at,
        open=value,high=value+1,low=value-1,close=value,volume=Decimal("1000"),source="test"))


def test_registry_has_required_stable_fields_and_public_sources():
    assert {x.id for x in SOURCE_REGISTRY}>={"KAP","AA","HABERTURK","EKONOMIM"}
    assert all(x.base_url.startswith("https://") and x.poll_interval>=300 for x in SOURCE_REGISTRY)


def test_company_alias_is_conservative_and_unmatched_stays_null():
    assert map_company_symbol("Turkish Airlines yeni uçuş")[0]=="THYAO"
    assert map_company_symbol("bizim için ekonomi haberi")[0] is None
    assert map_company_symbol("genel piyasa haberi")[0] is None


def test_source_failure_isolated_and_health_counters_persist():
    db=session();news_module._LAST_REFRESH_AT=0
    result=NewsService(db,config(),[Source("KAP",error=True),Source("AA",[record()])],Analyzer(),Notifier()).refresh()
    states={x.source:x for x in db.scalars(select(NewsSourceState)).all()}
    assert result["status"]=="PARTIAL" and result["new_items"]==1
    assert states["KAP"].status=="ERROR" and states["AA"].items_inserted==1
    assert db.scalar(select(func.count()).select_from(DataCollectionActivity))==2


def test_unmatched_is_saved_without_ai_quota_use():
    db=session();news_module._LAST_REFRESH_AT=0;analyzer=Analyzer()
    NewsService(db,config(),[Source("AA",[record(title="genel ekonomi gündemi")])],analyzer,Notifier()).refresh()
    item=db.scalar(select(NewsItem));assert item.symbol is None and item.ai_status=="SKIPPED_UNMATCHED" and analyzer.calls==0


def test_metrics_and_cross_feed_content_dedupe():
    db=session();news_module._LAST_REFRESH_AT=0;title="Turkish Airlines yeni uçuş"
    service=NewsService(db,config(),[Source("AA",[record("AA","1",title)]),Source("HABERTURK",[record("HABERTURK","2",title)])],Analyzer(),Notifier())
    service.refresh();metrics=service.metrics()
    assert metrics["total_news"]==1 and metrics["symbol_linked"]==1 and metrics["reactions_partial"]==1


def test_duplicate_refresh_revokes_legacy_false_symbol_mapping():
    db=session();at=datetime.now(timezone.utc)
    item=NewsItem(symbol="BIZIM",source="AA",source_id="legacy",title="genel haber",content="bizim için önemli",
        url="https://www.aa.com.tr/tr/legacy",category="OTHER",published_at=at,content_hash="legacy-hash")
    db.add(item);db.flush();db.add(NewsMarketReaction(news_id=item.id,symbol="BIZIM",status="WAITING_1D"));db.commit()
    news_module._LAST_REFRESH_AT=0
    row=NewsRecord("AA","legacy","genel haber","bizim için önemli","https://www.aa.com.tr/tr/legacy",at)
    NewsService(db,config(),[Source("AA",[row])],Analyzer(),Notifier()).refresh()
    assert db.get(NewsItem,item.id).symbol is None
    reaction=db.scalar(select(NewsMarketReaction));assert reaction.status=="ERROR" and reaction.error=="SYMBOL_MAPPING_REVOKED"


def test_backfill_news_never_notifies():
    class CountingNotifier:
        calls=0
        def send(self,item):self.calls+=1;return True
    db=session();news_module._LAST_REFRESH_AT=0;notifier=CountingNotifier()
    NewsService(db,config(),[Source("AA",[record()])],Analyzer(),notifier).refresh(backfill=True)
    item=db.scalar(select(NewsItem));assert item.telegram_eligible is False and item.telegram_sent is False and notifier.calls==0


def test_reaction_waits_for_market_open_and_is_due_bounded():
    db=session();item=add_news(db,datetime.now(timezone.utc),True)
    db.add(NewsMarketReaction(news_id=item.id,symbol="THYAO",status="PENDING",next_evaluation_at=datetime.now(timezone.utc)))
    db.commit();result=MarketMemoryService(db,config()).evaluate_reactions(limit=1)
    reaction=db.scalar(select(NewsMarketReaction));assert result["evaluated"]==1 and reaction.status=="WAITING_MARKET_OPEN" and reaction.next_evaluation_at


def test_reaction_progress_and_market_memory_health():
    db=session();item=add_news(db,datetime.now(timezone.utc))
    db.add(NewsMarketReaction(news_id=item.id,symbol="THYAO",status="COMPLETE"));db.commit()
    service=MarketMemoryService(db,config());status=service.reaction_status();health=service.health()
    assert status["complete"]==1 and status["progress_pct"]==100 and health["timeframes"]=={"5m":0,"15m":0,"1h":0,"1d":0}


def test_backfill_status_exposes_progress_and_timeframes():
    db=session();now=datetime.now(timezone.utc)
    for tf in ("5m","15m","1h","1d"):add_candle(db,tf,now,100)
    db.commit();status=BackfillService(db,config(),symbols=["THYAO","ASELS"]).status()
    assert status["universe_total"]==2 and status["candle_count"]==4 and status["timeframes"]["15m"]==1


def test_collection_center_endpoints_return_live_db_state():
    db=session();add_news(db,datetime.now(timezone.utc));db.add(DataCollectionActivity(module="NEWS",subject="AA",action="FETCH",status="OK",detail="1 new"));db.commit()
    app=FastAPI();app.include_router(router,prefix="/api");app.dependency_overrides[get_db]=lambda:db
    with TestClient(app) as client:
        assert client.get("/api/news/metrics").json()["total_news"]==1
        assert isinstance(client.get("/api/news/sources/health").json(),list)
        assert client.get("/api/news/reactions/status").status_code==200
        assert client.get("/api/data-collection/activity").json()[0]["module"]=="NEWS"


def test_company_master_covers_every_scanner_symbol():
    from app.market_data.symbols import BIST100_SYMBOLS
    assert set(COMPANY_MASTER)==set(BIST100_SYMBOLS) and len(COMPANY_MASTER)==100


def test_turkish_normalization_and_legal_suffix_cleanup():
    assert normalize_company_text("  ŞİŞECAM, Anonim Şirketi! ",strip_legal_suffixes=True)=="sisecam"


@pytest.mark.parametrize(("text","symbol"),[
    ("Türk Hava Yolları yeni uçuşlara başladı","THYAO"),
    ("Garanti-BBVA dijital bankacılık yatırımı","GARAN"),
    ("BİM'DE profesyonel oyuncu ekipmanı","BIMAS"),
    ("Mavi yeni mağaza açtı","MAVI"),
    ("ASELS savunma sözleşmesi açıkladı","ASELS"),
])
def test_alias_turkish_punctuation_short_name_and_ticker(text,symbol):
    result=resolve_company_symbols(text)
    assert result.primary_symbol==symbol and result.best_confidence>=85


def test_official_name_is_full_confidence():
    result=resolve_company_symbols("Türkiye Garanti Bankası A.Ş. açıklama yayımladı")
    assert result.primary_symbol=="GARAN" and result.links[0].match_method=="OFFICIAL_NAME" and result.links[0].confidence==100


def test_multi_company_news_has_deterministic_primary_and_all_links():
    result=resolve_company_symbols("Ford Otosan ile Koç Holding ortak yatırım açıkladı")
    assert result.primary_symbol=="FROTO" and {x.symbol for x in result.links}=={"FROTO","KCHOL"}


def test_ambiguous_group_word_is_not_linked():
    assert resolve_company_symbols("Anadolu şirketleri büyümeye devam ediyor").primary_symbol is None


def test_equal_strength_ambiguous_alias_is_reported_not_linked(monkeypatch):
    monkeypatch.setitem(COMPANY_MASTER,"TEST1",CompanyIdentity("TEST1","Test Bir A.Ş.","Ortak Marka"))
    monkeypatch.setitem(COMPANY_MASTER,"TEST2",CompanyIdentity("TEST2","Test İki A.Ş.","Ortak Marka"))
    result=resolve_company_symbols("Ortak Marka açıklama yaptı")
    assert result.primary_symbol is None and result.unmatched_reason=="AMBIGUOUS_MATCH"


@pytest.mark.parametrize("text",[
    "bizim için çok önemliydi", "genel piyasa ve enflasyon haberi", "Baykar ihracat rekoru açıkladı",
])
def test_negative_false_positive_samples_remain_unmatched(text):
    assert resolve_company_symbols(text).primary_symbol is None


def test_sponsorship_boilerplate_is_not_a_company_news_link():
    assert resolve_company_symbols("Günün gelişmeleri Halkbank'ın katkılarıyla").primary_symbol is None


def test_low_confidence_candidate_is_not_auto_linked():
    result=resolve_company_symbols("Türkiye Garanti Bankasının sonuçları")
    assert result.primary_symbol is None and result.unmatched_reason=="LOW_CONFIDENCE" and result.best_confidence==80


def test_reconciliation_is_bounded_restart_safe_and_creates_links_and_reactions():
    db=session();now=datetime.now(timezone.utc)
    for index,title in enumerate(("BİM'DE kampanya","Ford Otosan ile Koç Holding yatırım yaptı","genel gündem"),1):
        db.add(NewsItem(source="AA",source_id=f"r{index}",title=title,content="",url=f"https://example.com/{index}",
            category="OTHER",published_at=now,content_hash=f"reconcile-{index}"))
    db.commit();service=NewsService(db,config())
    first=service.reconcile_symbols(2)
    assert first["processed"]==2 and first["pending"]==1 and first["reactions_created"]==3
    assert db.scalar(select(func.count()).select_from(NewsCompanyLink))==3
    second=service.reconcile_symbols(2);third=service.reconcile_symbols(2)
    assert second["processed"]==1 and second["pending"]==0 and third["processed"]==0


def test_reconciliation_revokes_legacy_bizim_false_match_without_telegram():
    db=session();now=datetime.now(timezone.utc)
    item=NewsItem(symbol="BIZIM",source="AA",source_id="bad",title="JETEX terminali",content="bizim için önemliydi",
        url="https://example.com/bad",category="OTHER",published_at=now,content_hash="bad",telegram_sent=False)
    db.add(item);db.flush();db.add(NewsMarketReaction(news_id=item.id,symbol="BIZIM",status="WAITING_1D"));db.commit()
    result=NewsService(db,config()).reconcile_symbols(25);db.refresh(item)
    reaction=db.scalar(select(NewsMarketReaction))
    assert result["processed"]==1 and item.symbol is None and item.telegram_sent is False
    assert reaction.status=="ERROR" and reaction.error=="SYMBOL_MAPPING_REVOKED"


def test_reconciliation_api_and_unmatched_reason_metrics():
    db=session();now=datetime.now(timezone.utc)
    db.add(NewsItem(source="AA",source_id="api-r",title="genel ekonomi",content="",url="https://example.com/r",
        category="OTHER",published_at=now,content_hash="api-r"));db.commit()
    app=FastAPI();app.include_router(router,prefix="/api");app.dependency_overrides[get_db]=lambda:db
    with TestClient(app) as client:
        assert client.post("/api/news/reconcile-symbols?batch_size=1").json()["processed"]==1
        assert client.get("/api/news/unmatched").json()[0]["unmatched_reason"]=="NO_COMPANY_MATCH"
        metrics=client.get("/api/news/metrics").json()
        assert metrics["reconcile_pending"]==0 and metrics["unmatched_reasons"]["NO_COMPANY_MATCH"]==1
