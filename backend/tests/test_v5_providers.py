import gzip
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from app.config.settings import AppSettings
from app.market_data.cache import HistoricalCandleCache, redact_secret
from app.market_data.eodhd_provider import EodhdHistoricalProvider
from app.market_data.provider import CandleData, DataValidationError
from app.market_data.twelvedata_provider import TwelveDataProvider
from app.market_data.market_session import BistMarketSession
from app.research.provider_qualification import cross_provider_comparison, daily_intraday_continuity
from app.research.generalization import evaluate_v3_findings
from app.research.small_mid_qualification import select_small_mid_symbols, summarize_small_mid, symbol_result
from app.scanner.bist_scanner import get_provider


def candle(at, price=100):
    value=Decimal(str(price))
    return CandleData(at,value,value+1,value-1,value,Decimal("1000"),True)


def test_twelve_data_native_mapping_uses_xist_metadata():
    def handler(request):
        assert request.url.params["country"]=="Turkey"
        return httpx.Response(200,json={"data":[{"symbol":"ASELS","exchange":"Borsa Istanbul","mic_code":"XIST"}]})
    provider=TwelveDataProvider("secret",client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.resolve_symbol("ASELS.IS")["mic_code"]=="XIST"


def test_twelve_data_discovery_keeps_xist_common_stocks_only():
    def handler(_request):
        return httpx.Response(200,json={"data":[
            {"symbol":"AKSA","exchange":"Borsa Istanbul","mic_code":"XIST","type":"Common Stock"},
            {"symbol":"ETF1","exchange":"Borsa Istanbul","mic_code":"XIST","type":"ETF"},
            {"symbol":"OTHER","exchange":"Other","mic_code":"XXXX","type":"Common Stock"}]})
    provider=TwelveDataProvider("secret",client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert [row["symbol"] for row in provider.discover_xist_symbols()]==["AKSA"]


def test_small_mid_discovery_excludes_bist30_and_is_deterministic():
    metadata=[{"symbol":symbol} for symbol in ("ASELS","AKSA","ALARK","CIMSA","DOAS","MAVI")]
    selected,excluded=select_small_mid_symbols(metadata,target=20)
    assert "ASELS" not in selected and "ASELS" in excluded
    assert selected==["AKSA","ALARK","CIMSA","DOAS","MAVI"]


def test_twelve_data_timezone_metadata_normalizes_to_utc_and_requests_raw_prices():
    values=[]
    start=datetime(2026,1,5,10,0)
    for index in range(35):
        values.append({"datetime":(start+timedelta(minutes=15*index)).isoformat(sep=" "),"open":"100","high":"101","low":"99","close":"100","volume":"10"})
    def handler(request):
        if request.url.path.endswith("/stocks"):
            return httpx.Response(200,json={"data":[{"symbol":"ASELS","exchange":"Borsa Istanbul","mic_code":"XIST"}]})
        assert request.url.params["interval"]=="15min" and request.url.params["adjust"]=="none"
        return httpx.Response(200,json={"meta":{"exchange_timezone":"Europe/Istanbul"},"values":values})
    provider=TwelveDataProvider("secret",client=httpx.Client(transport=httpx.MockTransport(handler)))
    rows=provider.get_candles("ASELS","15m")
    assert rows[0].timestamp.tzinfo==timezone.utc
    assert rows[0].timestamp.hour==10  # intraday request explicitly asks Twelve Data for UTC


def test_api_secrets_are_redacted_from_urls_and_errors():
    text="GET /time_series?symbol=ASELS&apikey=super-secret&x=1 api_token=other-secret"
    safe=redact_secret(text)
    assert "super-secret" not in safe and "other-secret" not in safe
    assert safe.count("***")==2


def test_historical_cache_reuses_rows_and_verifies_checksum(tmp_path):
    cache=HistoricalCandleCache(tmp_path);calls=[];at=datetime(2026,1,1,tzinfo=timezone.utc)
    fetch=lambda:(calls.append(1) or [candle(at)])
    first,meta1=cache.get_or_fetch("test","ASELS","15m",at,at,fetch)
    second,meta2=cache.get_or_fetch("test","ASELS","15m",at,at,fetch)
    assert first==second and len(calls)==1 and not meta1["cache_hit"] and meta2["cache_hit"]
    path=next(tmp_path.glob("*.json.gz"))
    with gzip.open(path,"rt",encoding="utf-8") as handle:payload=json.load(handle)
    payload["rows"][0]["close"]="999"
    with gzip.open(path,"wt",encoding="utf-8") as handle:json.dump(payload,handle)
    with pytest.raises(ValueError,match="checksum"):
        cache.get_or_fetch("test","ASELS","15m",at,at,fetch)


def test_cross_provider_alignment_uses_canonical_utc_identity():
    at=datetime(2026,1,1,7,tzinfo=timezone.utc)
    left={"ASELS":{"15m":[candle(at,100)]}};right={"ASELS":{"15m":[candle(at,100.1)]}}
    report=cross_provider_comparison("a",left,"b",right,minimum_common=1)
    assert report["status"]=="PASS" and report["common_candles"]==1
    assert report["differences"]["close"]["mean_absolute_pct"]==.1


def test_provider_adjustment_semantics_are_raw():
    assert TwelveDataProvider.price_adjustment=="none"
    assert EodhdHistoricalProvider.price_adjustment=="none"


def test_daily_and_raw_intraday_continuity_guard_flags_large_adjustment_gap():
    at=datetime(2026,1,5,12,tzinfo=timezone.utc)
    frames={"15m":[candle(at,100)],"1d":[candle(at,50)]}
    report=daily_intraday_continuity(frames,BistMarketSession())
    assert report["status"]=="REVIEW" and report["large_discontinuities"]==1


def test_missing_paid_provider_credential_fails_closed_without_mock_fallback():
    with pytest.raises(DataValidationError,match="TWELVE_DATA_API_KEY"):
        get_provider(AppSettings(data_mode="live",market_data_provider="twelvedata",twelve_data_api_key=None))


def test_generalization_labels_insufficient_samples_without_inventing_conclusions():
    result=evaluate_v3_findings([])
    assert all(row["label"]=="INSUFFICIENT SAMPLE" for row in result.values())


def test_small_mid_symbol_report_and_25_symbol_gate():
    start=datetime(2026,1,2,10,tzinfo=timezone.utc)
    intraday=[candle(start+timedelta(days=index)) for index in range(20)]
    frames={"15m":intraday,"1h":[candle(start)],"1d":[candle(start)]}
    row=symbol_result("AKSA",frames,BistMarketSession())
    assert row["reaches_2026_02_01"] and row["status"]=="OOS_CAPABLE" and row["latest_price"]==100
    rows=[{**row,"symbol":f"SM{index:02d}"} for index in range(25)]
    report=summarize_small_mid([item["symbol"] for item in rows],rows,[],["ASELS"])
    assert report["status"]=="PASS" and report["15m_oos_capable"]==25
