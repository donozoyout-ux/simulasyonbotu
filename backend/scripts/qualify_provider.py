import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.market_data.cache import HistoricalCandleCache, redact_secret
from app.market_data.eodhd_provider import EodhdHistoricalProvider
from app.market_data.market_session import BistMarketSession
from app.market_data.twelvedata_provider import TwelveDataProvider
from app.market_data.yahoo_provider import YahooMarketDataProvider
from app.research.provider_qualification import cross_provider_comparison, qualify_results
from app.research.small_mid_qualification import select_small_mid_symbols, symbol_result, summarize_small_mid
from app.research.v4 import V3_RESEARCH_START, strategy_config_snapshot
from app.config.settings import AppSettings


SYMBOLS=["ASELS","THYAO","BIMAS","KCHOL","TUPRS","EREGL","AKBNK","GARAN","FROTO","SISE"]
OOS_START=datetime(2026,2,1,tzinfo=timezone.utc);OOS_END=datetime(2026,5,31,23,59,tzinfo=timezone.utc)
SMALL_MID_WARMUP_START=datetime(2025,11,1,tzinfo=timezone.utc)


def qualify(name, provider, cache):
    session=BistMarketSession();results={};errors=[];latencies=[];cache_latencies=[];mapping={}
    for symbol in SYMBOLS:
        frames={}
        try:
            if hasattr(provider,"resolve_symbol"): mapping[symbol]=provider.resolve_symbol(symbol)
            else: mapping[symbol]={"symbol":f"{symbol}.IS" if name=="yahoo" else symbol,"mapping_source":"provider_convention"}
            for timeframe in ("15m","1h","1d"):
                started=perf_counter()
                candles,meta=cache.get_or_fetch(name,symbol,timeframe,getattr(provider,"start",None),getattr(provider,"end",None),
                    lambda symbol=symbol,timeframe=timeframe:provider.get_candles(symbol,timeframe,10000))
                elapsed=(perf_counter()-started)*1000
                if meta.get("fetch_latency_ms") is not None:latencies.append(meta["fetch_latency_ms"])
                if meta.get("cache_hit"):cache_latencies.append(elapsed)
                frames[timeframe]=candles
            results[symbol]=frames
        except Exception as exc: errors.append({"symbol":symbol,"error":redact_secret(str(exc)),"rate_limited":"429" in str(exc)})
    report=qualify_results(name,SYMBOLS,results,errors,latencies,session);report["ticker_mapping"]=mapping
    report["cache_read_latency_ms"]={"mean":round(sum(cache_latencies)/len(cache_latencies),2) if cache_latencies else None,
        "max":round(max(cache_latencies),2) if cache_latencies else None,"hits":len(cache_latencies)}
    report["adjustment_semantics"]=getattr(provider,"price_adjustment","provider_raw_chart_ohlc")
    return report,results


def qualify_small_mid_twelve(api_key,cache):
    provider=TwelveDataProvider(api_key,SMALL_MID_WARMUP_START,OOS_END);session=BistMarketSession()
    try:
        discovered=provider.discover_xist_symbols()
        requested,excluded=select_small_mid_symbols(discovered,40)
    except Exception as exc:
        return summarize_small_mid([],[],[{"symbol":None,"error":redact_secret(str(exc)),"rate_limited":"429" in str(exc)}],[])
    rows=[];errors=[]
    for symbol in requested:
        frames={}
        for timeframe in ("15m","1h","1d"):
            try:
                candles,_=cache.get_or_fetch("twelvedata",symbol,timeframe,SMALL_MID_WARMUP_START,OOS_END,
                    lambda symbol=symbol,timeframe=timeframe:provider.get_candles(symbol,timeframe,10000))
                frames[timeframe]=candles
            except Exception as exc:
                errors.append({"symbol":symbol,"timeframe":timeframe,"error":redact_secret(str(exc)),
                    "rate_limited":"429" in str(exc)})
        rows.append(symbol_result(symbol,frames,session))
    return summarize_small_mid(requested,rows,errors,excluded)


def main():
    data_dir=Path(__file__).resolve().parents[1]/"data";cache=HistoricalCandleCache(data_dir/"provider_cache")
    providers={"yahoo":YahooMarketDataProvider()};waiting={}
    eodhd=os.getenv("EODHD_API_TOKEN");twelve=os.getenv("TWELVE_DATA_API_KEY")
    if eodhd:providers["eodhd"]=EodhdHistoricalProvider(eodhd,OOS_START,OOS_END)
    else:waiting["eodhd"]={"status":"WAITING_FOR_PROVIDER_CREDENTIAL","secret":"EODHD_API_TOKEN"}
    if twelve:providers["twelvedata"]=TwelveDataProvider(twelve,OOS_START,OOS_END)
    else:waiting["twelvedata"]={"status":"WAITING_FOR_PROVIDER_CREDENTIAL","secret":"TWELVE_DATA_API_KEY"}
    reports={};frames={}
    for name,provider in providers.items():reports[name],frames[name]=qualify(name,provider,cache)
    reports.update(waiting)
    small_mid=(qualify_small_mid_twelve(twelve,cache) if twelve else
        {"status":"WAITING_FOR_PROVIDER_CREDENTIAL","secret":"TWELVE_DATA_API_KEY",
         "small_mid_symbols_requested":0,"small_mid_valid":0,"15m_oos_capable":0,"failed":0,
         "oos_capable_symbols":[],"failed_symbols":[],"illiquid_or_low_quality_symbols":[],"symbols":[]})
    provider_candidates=[name for name,row in reports.items() if row.get("status")=="PASS"]
    qualification_complete=small_mid.get("status")=="PASS"
    qualified=provider_candidates if qualification_complete else []
    cross=[]
    for index,left in enumerate(provider_candidates):
        for right in provider_candidates[index+1:]:cross.append(cross_provider_comparison(left,frames[left],right,frames[right]))
    frozen=strategy_config_snapshot(AppSettings(auto_scan_enabled=False,strategy_version="v3"))
    expected="9185f9ce56652997aaa33041fb7d386c918b93fe203cd91b39f15a0ebd93bbeb"
    status="READY" if qualified else "PARTIAL" if small_mid.get("status")=="PARTIAL" else "WAITING_FOR_PROVIDER_CREDENTIAL" if waiting else "BLOCKED"
    payload={"status":status,"created_at":datetime.now(timezone.utc).isoformat(),"providers":reports,
        "twelvedata_small_mid":small_mid,
        "replay_gate":{"status":"PASS" if qualification_complete else "BLOCKED",
            "reason":None if qualification_complete else "Twelve Data small/mid qualification requires at least 25 OOS-capable symbols"},
        "selected_primary":qualified[0] if qualified else None,"validation_provider":qualified[1] if len(qualified)>1 else None,
        "selection_basis":"coverage, depth, integrity, session alignment, stability, reproducibility; never PnL",
        "cross_provider":cross,"oos_requirement":{"start":OOS_START.isoformat(),"end":OOS_END.isoformat(),"must_end_before":V3_RESEARCH_START.isoformat()},
        "config_hash":{"expected":expected,"actual":frozen["sha256"],"status":"PASS" if frozen["sha256"]==expected else "FAIL_CONFIG_DRIFT"}}
    output=data_dir/"v5_provider_qualification.json";output.write_text(json.dumps(payload,indent=2,ensure_ascii=False,sort_keys=True),encoding="utf-8")
    print(json.dumps({"output":str(output),"status":status,"selected_primary":payload["selected_primary"],"config_hash":payload["config_hash"]},indent=2))


if __name__=="__main__":main()
