import json
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import AppSettings
from app.db.session import Base
from app.market_data.yahoo_provider import YahooMarketDataProvider
from app.replay.engine import ReplayEngine


def encode(value):
    if isinstance(value,Decimal):return float(value)
    raise TypeError


def main():
    config=AppSettings(data_mode="live",auto_scan_enabled=False)
    provider=YahooMarketDataProvider();symbols=["ASELS","THYAO","TUPRS","BIMAS","EREGL"]
    reports=[]
    for symbol in symbols:
        try:
            frames={tf:provider.get_candles(symbol,tf,220) for tf in ("1d","1h","15m")}
            engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine)
            with Session(engine) as db:reports.append(ReplayEngine(db,config).run(symbol,frames))
        except Exception as exc:reports.append({"symbol":symbol,"error":str(exc)})
    print(json.dumps(reports,default=encode,indent=2))


if __name__=="__main__":main()

