import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import AppSettings
from app.db.session import Base
from app.replay.portfolio_engine import PortfolioReplayEngine
from app.research.dataset import load_dataset
from app.research.diagnostics import analyze_dataset
from app.research.generalization import evaluate_v3_findings
from app.research.v4 import analyze_oos_records, assert_oos_period, audit_dataset, strategy_config_snapshot


EXPECTED_CONFIG_HASH = "9185f9ce56652997aaa33041fb7d386c918b93fe203cd91b39f15a0ebd93bbeb"


def main():
    data_dir = Path(__file__).resolve().parents[1] / "data"
    dataset_path = data_dir / "v5_oos_canonical.json.gz"
    if not dataset_path.exists():
        raise RuntimeError("WAITING_FOR_PROVIDER_CREDENTIAL: V5 canonical OOS dataset is unavailable")
    metadata, frames, benchmark = load_dataset(dataset_path)
    evaluation_start = datetime.fromisoformat(metadata["evaluation_start"]).astimezone(timezone.utc)
    evaluation_end = datetime.fromisoformat(metadata["evaluation_end"]).astimezone(timezone.utc)
    assert_oos_period(evaluation_start, evaluation_end)
    config = AppSettings(data_mode="live", auto_scan_enabled=False, strategy_version="v3")
    frozen = strategy_config_snapshot(config)
    if frozen["sha256"] != EXPECTED_CONFIG_HASH:
        raise RuntimeError("FAIL_CONFIG_DRIFT")

    research = analyze_dataset(frames, config, step=4,
                               evaluation_start=evaluation_start, evaluation_end=evaluation_end)
    records=research["records"]
    validation = analyze_oos_records(records, config.entry_score, float(config.min_rr))
    generalization=evaluate_v3_findings(records)
    research.pop("records", None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        portfolio = PortfolioReplayEngine(db, config).run(
            frames, benchmark, 4, evaluation_start=evaluation_start, evaluation_end=evaluation_end
        )
    if portfolio["strategy_config_hash"] != EXPECTED_CONFIG_HASH:
        raise RuntimeError("FAIL_CONFIG_DRIFT")
    confidence = ("VERY LOW" if portfolio["trades"] < 10 else "LOW" if portfolio["trades"] < 30
                  else "LIMITED" if portfolio["trades"] < 100 else "MORE_USEFUL")
    payload = {
        "status": "READY", "immutable": True, "dataset": metadata, "strategy_config": frozen,
        "data_integrity": audit_dataset(frames, metadata["source"]), "research": research,
        "oos_validation": validation, "v3_to_v5_generalization":generalization,
        "portfolio_oos": portfolio, "sample_confidence": confidence,
    }
    output = data_dir / "v5_oos_report.json"
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(output), "status": "READY", "trades": portfolio["trades"],
                      "sample_confidence": confidence}, indent=2))


if __name__ == "__main__":
    main()
