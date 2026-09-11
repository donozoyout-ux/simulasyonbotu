import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import AppSettings
from app.db.session import Base
from app.replay.portfolio_engine import PortfolioReplayEngine
from app.research.dataset import load_dataset


def main():
    data_dir=Path(__file__).resolve().parents[1]/"data"
    metadata,frames,benchmark=load_dataset(data_dir/"v3_canonical.json.gz")
    engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        report=PortfolioReplayEngine(db,AppSettings(data_mode="live",auto_scan_enabled=False,strategy_version="v3")).run(frames,benchmark,4)
    payload={"dataset":{"sha256":metadata["sha256"],"symbols":metadata["symbols"],"start":metadata["start"],"end":metadata["end"]},"portfolio_replay":report}
    output=data_dir/"v3_portfolio_report.json"
    with output.open("w",encoding="utf-8") as handle:
        json.dump(payload,handle,indent=2,ensure_ascii=False,default=lambda value:value.isoformat() if isinstance(value,datetime) else float(value))
    print(json.dumps({"output":str(output),**{key:value for key,value in report.items() if key not in ("capital_diagnostics","trade_details","allocation_timeline")}},indent=2))


if __name__=="__main__":main()
