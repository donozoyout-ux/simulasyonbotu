import json
from datetime import datetime
from pathlib import Path

from app.config.settings import AppSettings
from app.research.dataset import load_dataset
from app.research.diagnostics import analyze_dataset


def main():
    data_dir=Path(__file__).resolve().parents[1]/"data"
    metadata,frames,_=load_dataset(data_dir/"v3_canonical.json.gz")
    report=analyze_dataset(frames,AppSettings(data_mode="live",auto_scan_enabled=False,strategy_version="v3"),step=4)
    report.pop("records",None)
    payload={"dataset":metadata,"strategy_version":"v3","research":report}
    output=data_dir/"v3_after_report.json"
    with output.open("w",encoding="utf-8") as handle:
        json.dump(payload,handle,indent=2,ensure_ascii=False,
                  default=lambda value:value.isoformat() if isinstance(value,datetime) else float(value))
    print(json.dumps({"output":str(output),"observations":report["observations"],"score":report["score"],
        "sequential_funnel":report["sequential_funnel"],"setup_funnels":report["setup_funnels"],
        "component_utilization":report["component_utilization"]},indent=2))


if __name__=="__main__":main()
