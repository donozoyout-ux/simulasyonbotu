from __future__ import annotations

from collections import Counter
from datetime import time
from statistics import mean

from app.research.v4 import V3_RESEARCH_START


def candle_quality(candles, session, timeframe="15m") -> dict:
    identities=[c.timestamp for c in candles]; duplicates=len(identities)-len(set(identities))
    invalid=sum(c.low<=0 or c.high<c.low or c.high<max(c.open,c.close) or c.low>min(c.open,c.close) for c in candles)
    zero_volume=sum(c.volume==0 for c in candles)
    local=[c.timestamp.astimezone(session.tz) for c in candles]
    outside=sum(not (time(9,30)<=stamp.time()<=session.close_time) for stamp in local) if timeframe != "1d" else 0
    daily=Counter(stamp.date() for stamp in local)
    expected=Counter(daily.values()).most_common(1)[0][0] if daily else 0
    missing=sum(max(0,expected-count) for count in daily.values())
    expected_total=sum(daily.values())+missing
    return {"rows":len(candles),"earliest":min(identities).isoformat() if identities else None,
        "latest":max(identities).isoformat() if identities else None,"duplicate_rate_pct":round(duplicates/len(candles)*100,6) if candles else None,
        "invalid_ohlc_rate_pct":round(invalid/len(candles)*100,6) if candles else None,
        "zero_volume_rate_pct":round(zero_volume/len(candles)*100,6) if candles else None,
        "missing_rate_pct":round(missing/expected_total*100,6) if expected_total else None,
        "session_mismatch_rate_pct":round(outside/len(candles)*100,6) if candles else None,
        "timezone_aware":all(c.timestamp.tzinfo is not None for c in candles),
        "strictly_increasing":all(left < right for left,right in zip(identities,identities[1:])),
        "pre_v3_trading_days":len({stamp.date() for stamp in local if stamp.astimezone(V3_RESEARCH_START.tzinfo)<V3_RESEARCH_START})}


def daily_intraday_continuity(frames, session, warning_threshold_pct=10.0) -> dict:
    intraday_by_day={}
    for candle in frames.get("15m",[]):
        intraday_by_day.setdefault(candle.timestamp.astimezone(session.tz).date(),[]).append(candle)
    comparisons=[]
    for daily in frames.get("1d",[]):
        day=daily.timestamp.astimezone(session.tz).date()
        intraday=intraday_by_day.get(day)
        if not intraday or daily.close == 0:continue
        close=sorted(intraday,key=lambda item:item.timestamp)[-1].close
        comparisons.append(abs(float((close-daily.close)/daily.close*100)))
    warnings=sum(value > warning_threshold_pct for value in comparisons)
    return {"common_sessions":len(comparisons),"warning_threshold_pct":warning_threshold_pct,
        "mean_close_difference_pct":round(mean(comparisons),6) if comparisons else None,
        "max_close_difference_pct":round(max(comparisons),6) if comparisons else None,
        "large_discontinuities":warnings,"status":"PASS" if comparisons and not warnings else "REVIEW" if comparisons else "INSUFFICIENT_COMMON_SESSIONS"}


def qualify_results(provider: str, requested_symbols: list[str], results: dict, errors: list[dict], latencies: list[float], session) -> dict:
    valid_symbols=sorted({symbol for symbol,frames in results.items() if all(frames.get(tf) for tf in ("15m","1h","1d"))})
    metrics={symbol:{tf:candle_quality(candles,session,tf) for tf,candles in frames.items()} for symbol,frames in results.items()}
    continuity={symbol:daily_intraday_continuity(frames,session) for symbol,frames in results.items()}
    depth=[metrics[symbol]["15m"]["pre_v3_trading_days"] for symbol in valid_symbols]
    enough=bool(depth) and min(depth)>=60
    status="PASS" if len(valid_symbols)==len(requested_symbols) and enough and not errors else "PARTIAL" if valid_symbols else "FAIL"
    return {"provider":provider,"status":status,"requested_symbols":len(requested_symbols),"valid_symbols":len(valid_symbols),
        "symbol_coverage_pct":round(len(valid_symbols)/len(requested_symbols)*100,4) if requested_symbols else None,
        "failed_symbols":[symbol for symbol in requested_symbols if symbol not in valid_symbols],"oos_depth_pass":enough,
        "minimum_pre_v3_15m_trading_days":min(depth) if depth else 0,"metrics":metrics,"errors":errors,
        "daily_intraday_price_continuity":continuity,
        "request_failure_rate_pct":round(len(errors)/(len(requested_symbols)*3)*100,4),
        "rate_limit_errors":sum(bool(error.get("rate_limited")) for error in errors),
        "response_latency_ms":{"mean":round(mean(latencies),2) if latencies else None,"max":round(max(latencies),2) if latencies else None},
        "selection_note":"Data quality only; PnL is never evaluated during provider qualification."}


def cross_provider_comparison(left_name, left_frames, right_name, right_frames, minimum_common=20) -> dict:
    common=[]
    for symbol in sorted(set(left_frames)&set(right_frames)):
        left={c.timestamp:c for c in left_frames[symbol].get("15m",[])};right={c.timestamp:c for c in right_frames[symbol].get("15m",[])}
        for stamp in sorted(set(left)&set(right)):
            common.append((symbol,stamp,left[stamp],right[stamp]))
    differences={field:[] for field in ("open","high","low","close","volume")}
    for _,_,left,right in common:
        for field in differences:
            a=float(getattr(left,field));b=float(getattr(right,field))
            if a: differences[field].append(abs(a-b)/abs(a)*100)
    return {"providers":[left_name,right_name],"common_candles":len(common),"status":"PASS" if len(common)>=minimum_common else "INSUFFICIENT_COMMON_CANDLES",
        "differences":{field:{"mean_absolute_pct":round(mean(values),6) if values else None,"max_pct":round(max(values),6) if values else None} for field,values in differences.items()},
        "volume_note":"Volume methodology may differ; OHLC and volume are reported separately."}
