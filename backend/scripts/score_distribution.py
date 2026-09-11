import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from statistics import median

from app.analysis.pipeline import analyze_frames
from app.config.settings import AppSettings
from app.market_data.symbols import BIST100_SYMBOLS
from app.market_data.yahoo_provider import YahooMarketDataProvider


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--limit",type=int,default=100)
    args=parser.parse_args()
    config=AppSettings(data_mode="live",auto_scan_enabled=False)
    at=datetime.now(timezone.utc)

    def analyze(symbol):
        provider=YahooMarketDataProvider()
        frames={tf:provider.get_candles(symbol,tf,220) for tf in ("1d","1h","15m")}
        result=analyze_frames(symbol,frames,config,"yahoo",at,False)
        return {"symbol":symbol,"score":result.score,"setup":result.setup,"decision":result.decision,
                "analysis_complete":result.details["analysis_complete"],"funnel":result.funnel}

    rows=[];errors=[]
    symbols=BIST100_SYMBOLS[:max(1,min(args.limit,len(BIST100_SYMBOLS)))]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(analyze,symbol):symbol for symbol in symbols}
        for future in as_completed(futures):
            try:rows.append(future.result())
            except Exception as exc:errors.append({"symbol":futures[future],"reason":str(exc)})
    scores=sorted(row["score"] for row in rows)
    def percentile(fraction):
        if not scores:return None
        index=(len(scores)-1)*fraction;lower=int(index);upper=min(lower+1,len(scores)-1);weight=index-lower
        return round(scores[lower]*(1-weight)+scores[upper]*weight,2)
    buckets={(0,49):0,(50,59):0,(60,69):0,(70,79):0,(80,89):0,(90,100):0}
    for score in scores:
        for (low,high) in buckets:
            if low<=score<=high:buckets[(low,high)]+=1;break
    funnel_keys=("data_valid","sufficient_history","htf_bullish","valid_setup","score_pass","rr_pass")
    report={"requested":len(symbols),"analyzed":len(rows),"failed":len(errors),
            "score_distribution":{f"{low}-{high}":count for (low,high),count in buckets.items()},
            "min_score":min(scores,default=None),"max_score":max(scores,default=None),
            "mean_score":round(sum(scores)/len(scores),2) if scores else None,"median_score":median(scores) if scores else None,
            "p25_score":percentile(.25),"p75_score":percentile(.75),
            "funnel":{key:sum(bool(row["funnel"][key]) for row in rows) for key in funnel_keys},
            "setups":dict(Counter(row["setup"] for row in rows)),"errors":sorted(errors,key=lambda item:item["symbol"]),
            "top":sorted(rows,key=lambda row:row["score"],reverse=True)[:10]}
    print(json.dumps(report,indent=2))


if __name__=="__main__":main()
