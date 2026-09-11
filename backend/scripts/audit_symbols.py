import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime,timezone
from app.config.settings import AppSettings
from app.market_data.market_session import BistMarketSession
from app.market_data.symbols import BIST100_SYMBOLS
from app.market_data.yahoo_provider import YahooMarketDataProvider


def main():
    config=AppSettings();provider=YahooMarketDataProvider();session=BistMarketSession.from_config(config)
    report={"total":len(BIST100_SYMBOLS),"valid":0,"invalid":[],"no_data":[],"stale":[],"duplicate":[],"negative_or_zero":[]}

    def audit(symbol):
        try:
            candles=provider.get_candles(symbol,"1d",40)
            if not candles:return symbol,"no_data",None
            timestamps=[c.timestamp for c in candles]
            if len(timestamps)!=len(set(timestamps)):return symbol,"duplicate",None
            if any(min(c.open,c.high,c.low,c.close)<=0 or c.volume<0 for c in candles):return symbol,"negative_or_zero",None
            if session.freshness(candles[-1].timestamp,"1d",datetime.now(timezone.utc),4320)=="STALE":return symbol,"stale",None
            return symbol,"valid",None
        except Exception as exc:return symbol,"invalid",str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(audit,symbol) for symbol in BIST100_SYMBOLS]
        for future in as_completed(futures):
            symbol,status,error=future.result()
            if status=="valid":report["valid"]+=1
            elif status=="invalid":report["invalid"].append({"symbol":symbol,"reason":error})
            else:report[status].append(symbol)
    for key in ("no_data","stale","duplicate","negative_or_zero"):report[key].sort()
    report["invalid"].sort(key=lambda item:item["symbol"])
    report["failed"]=len(report["invalid"])+len(report["no_data"])
    print(json.dumps(report,indent=2))


if __name__=="__main__":main()
