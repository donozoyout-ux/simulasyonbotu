from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from decimal import Decimal
from statistics import mean, median

from app.analysis.pipeline import analyze_frames
from app.market_data.market_session import BistMarketSession, TIMEFRAME_DELTA


COMPONENT_MAX = {"trend":20,"structure":15,"momentum":15,"volume":15,"levels":15,"setup":10,"risk_reward":10}
# 32 x 15m is one full eight-hour BIST trading session. The shorter
# horizons preserve the setup-calibration windows requested by V3.
HORIZONS = (1, 2, 4, 8, 16, 32)


def is_near_miss(diagnostic:dict)->bool:
    failed=diagnostic.get("failed_rules",[])
    return bool(diagnostic.get("candidate")) and not diagnostic.get("detected") and 0<len(failed)<=2


def sequential_counts(rows:list[dict],stages:tuple[str,...])->dict[str,int]:
    result=Counter()
    for row in rows:
        passed=True
        for stage in stages:
            passed=passed and bool(row.get(stage))
            result[stage]+=int(passed)
    return dict(result)


def stats(values):
    ordered=sorted(float(value) for value in values)
    if not ordered:return {"min":None,"max":None,"mean":None,"median":None,"p25":None,"p75":None}
    def percentile(fraction):
        index=(len(ordered)-1)*fraction;lower=int(index);upper=min(lower+1,len(ordered)-1);weight=index-lower
        return ordered[lower]*(1-weight)+ordered[upper]*weight
    return {"min":round(ordered[0],4),"max":round(ordered[-1],4),"mean":round(mean(ordered),4),
            "median":round(median(ordered),4),"p25":round(percentile(.25),4),"p75":round(percentile(.75),4)}


def score_bucket(score):
    if score<30:return "0-29"
    if score<40:return "30-39"
    if score<50:return "40-49"
    if score<60:return "50-59"
    if score<70:return "60-69"
    return "70+"


def analyze_dataset(frames_by_symbol, config, step=4, evaluation_start=None, evaluation_end=None):
    session=BistMarketSession.from_config(config)
    records=[];component_values=defaultdict(list);setup_counts={name:{"evaluated":0,"candidate":0,"rejected":0,"confirmed":0,"reasons":Counter()}
        for name in ("BREAKOUT","PULLBACK","SUPPORT_BOUNCE","TREND_CONTINUATION")}
    near_misses=defaultdict(list);rejections=Counter();sequential=Counter();snapshots=[]
    for symbol,frames in sorted(frames_by_symbol.items()):
        times={tf:[c.timestamp for c in rows] for tf,rows in frames.items()}
        trigger=frames["15m"]
        for index in range(80,len(trigger)-max(HORIZONS),step):
            current=trigger[index];analysis_at=current.timestamp+TIMEFRAME_DELTA["15m"]
            if evaluation_start is not None and current.timestamp < evaluation_start:continue
            if evaluation_end is not None and current.timestamp > evaluation_end:continue
            visible={}
            for tf,rows in frames.items():
                end=bisect_right(times[tf],analysis_at)
                candidates=rows[max(0,end-260):end]
                visible[tf]=[c for c in candidates if session.candle_is_closed(c.timestamp,tf,analysis_at)]
            if any(len(visible[tf])<60 for tf in visible):continue
            result=analyze_frames(symbol,visible,config.model_copy(update={"data_mode":"replay"}),"replay",analysis_at,False)
            details=result.details;breakdown=details["score_breakdown"]
            for key,value in breakdown.items():component_values[key].append(value)
            complete=bool(details["analysis_complete"]);htf=result.trend=="bullish" if config.strategy_version=="v2" else result.trend!="bearish";setup=result.funnel["valid_setup"]
            score_pass=result.score>=config.entry_score;rr_pass=result.funnel["rr_pass"]
            sequential["analysis_complete"]+=int(complete)
            sequential["htf_eligible"]+=int(complete and htf)
            sequential["valid_setup"]+=int(complete and htf and setup)
            sequential["score_pass"]+=int(complete and htf and setup and score_pass)
            sequential["rr_pass"]+=int(complete and htf and setup and score_pass and rr_pass)
            for label,passed in (("analysis_incomplete",complete),("htf_ineligible",htf),("setup_missing",setup),
                                 ("score_below_threshold",score_pass),("rr_below_minimum",rr_pass)):
                if not passed:rejections[label]+=1
            diagnostics=details["setup"].get("evidence",{}).get("diagnostics",{})
            for name,bucket in setup_counts.items():
                diagnostic=diagnostics.get(name)
                if not diagnostic:continue
                bucket["evaluated"]+=1;bucket["candidate"]+=int(bool(diagnostic.get("candidate")))
                bucket["confirmed"]+=int(bool(diagnostic.get("detected")));bucket["rejected"]+=int(not diagnostic.get("detected"))
                failed=diagnostic.get("failed_rules",[])
                bucket["reasons"].update(failed)
                if is_near_miss(diagnostic):
                    near_misses[name].append({"symbol":symbol,"timestamp":current.timestamp.isoformat(),
                        "quality":diagnostic.get("quality_score",0),"failed_rules":failed,"conditions":diagnostic.get("conditions",{})})
            forward={}
            for horizon in HORIZONS:
                future=trigger[index+horizon]
                forward[str(horizon)]=float((future.close/current.close-1)*100)
            future_window=trigger[index+1:index+17]
            mfe=float((max(c.high for c in future_window)/current.close-1)*100)
            mae=float((min(c.low for c in future_window)/current.close-1)*100)
            excursions={}
            for horizon in (4,16,32):
                window=trigger[index+1:index+horizon+1]
                excursions[str(horizon)]={"mfe":float((max(c.high for c in window)/current.close-1)*100),
                    "mae":float((min(c.low for c in window)/current.close-1)*100)}
            setup_detail=details["setup"]
            record={"symbol":symbol,"timestamp":current.timestamp.isoformat(),"score":result.score,"setup":result.setup,
                "setup_detected":setup,"htf":htf,"rr":float(details["risk_reward"]),"rvol":float(details["volume"].get("rvol") or 0),
                "atr":float(details["volatility"]["atr"]),"candle_quality":details["candle_quality"].get("quality_score",0),"breakdown":breakdown,
                "entry":float(setup_detail["entry_area"]),"stop":float(setup_detail["invalidation_level"]),
                "target":float(setup_detail["target"]),"forward_returns":forward,"mfe":mfe,"mae":mae,
                "excursions":excursions,"decision":result.decision}
            records.append(record)
            if setup or result.score>=config.entry_score:
                snapshots.append({**record,"failed_rules":[key for key in ("htf","setup","score","rr")
                    if not {"htf":htf,"setup":setup,"score":score_pass,"rr":rr_pass}[key]],
                    "levels":details["levels"],"swings":details["structure"]["swings"],
                    "candles":[{"timestamp":c.timestamp.isoformat(),"open":float(c.open),"high":float(c.high),"low":float(c.low),
                                "close":float(c.close),"volume":float(c.volume)} for c in visible["15m"][-40:]]})
    score_values=[row["score"] for row in records]
    utilization={key:{**stats(values),"possible_max":maximum,
        "observed_max_utilization_pct":round(max(values,default=0)/maximum*100,2)} for key,(values,maximum) in
        ((key,(component_values[key],value)) for key,value in COMPONENT_MAX.items())}
    by_score=defaultdict(list);by_rvol=defaultdict(list);by_setup=defaultdict(list)
    for row in records:
        by_score[score_bucket(row["score"])].append(row)
        rvol=row["rvol"];rvol_bucket="<1" if rvol<1 else "1-1.5" if rvol<1.5 else "1.5-2" if rvol<2 else ">=2"
        by_rvol[rvol_bucket].append(row)
        if row["setup_detected"]:by_setup[row["setup"]].append(row)
    def performance(groups):
        return {key:{"count":len(rows),**{f"return_{h}":round(mean(r["forward_returns"][str(h)] for r in rows),4) for h in HORIZONS},
            "mfe":round(mean(r["mfe"] for r in rows),4),"mae":round(mean(r["mae"] for r in rows),4)} for key,rows in sorted(groups.items())}
    sorted_by_score=sorted(records,key=lambda row:row["score"],reverse=True)
    percentiles={label:performance({label:sorted_by_score[:max(1,round(len(sorted_by_score)*fraction))]})[label]
                 for label,fraction in (("top_5_pct",.05),("top_10_pct",.10),("top_20_pct",.20))}
    setup_report={name:{**{key:value for key,value in bucket.items() if key!="reasons"},"reasons":dict(bucket["reasons"].most_common())}
                  for name,bucket in setup_counts.items()}
    feature_discrimination={}
    for component in COMPONENT_MAX:
        ordered=sorted(row["breakdown"][component] for row in records);low_cut=ordered[len(ordered)//4];high_cut=ordered[(len(ordered)*3)//4]
        feature_discrimination[component]=performance({"low": [row for row in records if row["breakdown"][component]<=low_cut],
                                                        "high":[row for row in records if row["breakdown"][component]>=high_cut]})
    feature_discrimination["candle_quality"]=performance({"low":[row for row in records if row["candle_quality"]<40],
        "medium":[row for row in records if 40<=row["candle_quality"]<70],"high":[row for row in records if row["candle_quality"]>=70]})
    filter_groups={"setup_only":[row for row in records if row["setup_detected"]],
        "score_only":[row for row in records if row["score"]>=config.entry_score],
        "setup_score":[row for row in records if row["setup_detected"] and row["score"]>=config.entry_score],
        "setup_score_htf":[row for row in records if row["setup_detected"] and row["score"]>=config.entry_score and row["htf"]],
        "setup_score_htf_rr":[row for row in records if row["setup_detected"] and row["score"]>=config.entry_score and row["htf"] and row["rr"]>=float(config.min_rr)]}
    return {"observations":len(records),"score":{**stats(score_values),"distribution":dict(Counter(score_bucket(v) for v in score_values))},
        "component_utilization":utilization,"sequential_funnel":dict(sequential),"independent_rejections":dict(rejections),
        "setup_funnels":setup_report,"near_misses":{key:sorted(rows,key=lambda row:row["quality"],reverse=True)[:20] for key,rows in near_misses.items()},
        "score_vs_forward":performance(by_score),"rvol_discrimination":performance(by_rvol),"setup_performance":performance(by_setup),
        "percentile_performance":percentiles,"feature_discrimination":feature_discrimination,
        "filter_comparison":performance(filter_groups),"snapshots":sorted(snapshots,key=lambda row:row["score"],reverse=True)[:50],"records":records}
