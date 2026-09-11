from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config.settings import AppSettings
from app.db.session import Base
from app.market_data.twelvedata_provider import TwelveDataProvider
from app.market_data.yahoo_provider import YahooMarketDataProvider
from app.models import NewsItem
from app.news.dedupe import content_hash
from app.news.models import NewsRecord
from app.news.security import validate_redirect, validate_source_url
from app.news.sentiment import GroqNewsAnalyzer
from app.news.service import NewsService
from app.news.sources.kap import parse_kap_html
from app.news.sources.kap import KapSource
from app.market_data.hybrid_provider import HybridMarketDataProvider
from app.market_data.provider import DataValidationError
from app.news.sources.rss import parse_rss
from app.news.telegram import TelegramNewsNotifier


def config(**updates):
    values={"news_enabled":True,"kap_enabled":True,"ai_enabled":True,"ai_provider":"groq"};values.update(updates)
    return AppSettings(_env_file=None,**values)


def db_session():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Session(engine,expire_on_commit=False)


def test_kap_valid_parse_and_category():
    rows=parse_kap_html('<a href="/tr/Bildirim/12345">THYAO Yeni sözleşme açıklaması</a>')
    assert len(rows)==1 and rows[0].source_id=="12345" and rows[0].symbol=="THYAO"
    assert rows[0].category=="NEW_CONTRACT"


def test_kap_malformed_and_empty_html_are_fail_safe():
    assert parse_kap_html("<broken>")==[]
    assert parse_kap_html("")==[]


def test_rss_valid_and_malformed():
    xml='<rss><channel><item><title>ASELS yatırım haberi</title><link>https://www.aa.com.tr/tr/ekonomi/test</link><guid>x1</guid></item></channel></rss>'
    assert parse_rss(xml)[0].symbol=="ASELS"
    with pytest.raises(Exception):parse_rss("<rss")


def test_content_hash_is_stable_and_sensitive():
    assert content_hash("KAP"," A ","B","https://x")==content_hash("kap","a","b","https://x")
    assert content_hash("KAP","A","B","https://x")!=content_hash("KAP","A","C","https://x")


class StaticSource:
    name="KAP"
    def __init__(self,rows):self.rows=rows
    def fetch(self):return self.rows


class StaticAnalyzer:
    def evaluate(self,_):return {"status":"OK","model":"openai/gpt-oss-20b","sentiment":"POSITIVE","importance":88,"summary":"Yalnız sağlanan açıklamanın özeti","horizon":"SHORT_TERM","risks":[],"tags":["contract"]}


class StaticNotifier:
    def __init__(self):self.calls=0
    def send(self,item):self.calls+=1;return True


def test_news_database_dedupe_and_telegram_dedupe():
    db=db_session();notifier=StaticNotifier();record=NewsRecord("KAP","42","ASELS sözleşme","metin","https://www.kap.org.tr/tr/Bildirim/42",datetime.now(timezone.utc),"ASELS")
    service=NewsService(db,config(),[StaticSource([record])],StaticAnalyzer(),notifier)
    assert service.refresh()["new_items"]==1
    assert service.refresh()["new_items"]==0
    assert db.scalar(select(func.count()).select_from(NewsItem))==1 and notifier.calls==1
    assert db.scalar(select(NewsItem)).telegram_sent is True


@pytest.mark.parametrize("url",["http://www.kap.org.tr/x","https://127.0.0.1/x","https://169.254.1.1/x","https://evil.example/x","https://user:pass@kap.org.tr/x"])
def test_ssrf_and_unsafe_urls_are_rejected(url):
    with pytest.raises(ValueError):validate_source_url(url)


def test_redirect_hostname_is_revalidated():
    with pytest.raises(ValueError):validate_redirect("https://kap.org.tr/x","https://localhost/x")


@pytest.mark.parametrize("status,expected",[(429,"RATE_LIMITED"),(401,"AUTH_ERROR"),(500,"AI_UNAVAILABLE")])
def test_groq_news_http_failures(status,expected):
    client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(status,request=request)))
    assert GroqNewsAnalyzer(config(groq_api_key="key"),client).evaluate({"title":"x"})["status"]==expected


def test_groq_news_valid_and_malformed_schema():
    valid='{"sentiment":"POSITIVE","importance":80,"summary":"özet","horizon":"SHORT_TERM","risks":[],"tags":[]}'
    client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"choices":[{"message":{"content":valid}}]},request=request)))
    assert GroqNewsAnalyzer(config(groq_api_key="key"),client).evaluate({"title":"x"})["importance"]==80
    bad=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"choices":[{"message":{"content":"{}"}}]},request=request)))
    assert GroqNewsAnalyzer(config(groq_api_key="key"),bad).evaluate({"title":"x"})["status"]=="MALFORMED_RESPONSE"


def test_groq_news_disabled_missing_key_and_timeout():
    assert GroqNewsAnalyzer(config(ai_enabled=False)).evaluate({})["status"]=="DISABLED"
    assert GroqNewsAnalyzer(config(groq_api_key=None)).evaluate({})["status"]=="API_KEY_MISSING"
    def timeout(request):raise httpx.ReadTimeout("slow",request=request)
    client=httpx.Client(transport=httpx.MockTransport(timeout))
    assert GroqNewsAnalyzer(config(groq_api_key="key"),client).evaluate({})["status"]=="AI_UNAVAILABLE"


def test_telegram_news_is_one_shot_and_never_sends_low_importance():
    sent=[]
    client=httpx.Client(transport=httpx.MockTransport(lambda request:(sent.append(request) or httpx.Response(200,request=request))))
    notifier=TelegramNewsNotifier(config(telegram_enabled=True,telegram_bot_token="token",telegram_chat_id="chat"),client)
    item=type("Item",(),{"source":"KAP","telegram_sent":False,"ai_importance":79,"symbol":"ASELS","title":"x","ai_sentiment":"POSITIVE","ai_summary":"s"})()
    assert notifier.send(item) is False and not sent
    item.ai_importance=88
    assert notifier.send(item) is True and len(sent)==1


def test_5m_provider_mappings():
    assert YahooMarketDataProvider.intervals["5m"]==("5m","60d")
    assert TwelveDataProvider.intervals["5m"]=="5min"


def test_kap_network_timeout_is_isolated():
    def timeout(request):raise httpx.ReadTimeout("slow",request=request)
    source=KapSource(client=httpx.Client(transport=httpx.MockTransport(timeout)))
    with pytest.raises(httpx.ReadTimeout):source.fetch()


def test_hybrid_5m_falls_back_to_yahoo():
    class Twelve:
        def get_candles(self,*_):raise DataValidationError("down")
    expected=[object()]
    class Yahoo:
        def get_candles(self,symbol,timeframe,limit):
            assert timeframe=="5m";return expected
    provider=HybridMarketDataProvider.__new__(HybridMarketDataProvider)
    provider.twelve,provider.yahoo=Twelve(),Yahoo();provider.last_sources={};provider.last_validation={}
    assert provider.get_candles("ASELS","5m",50) is expected
