import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import AppSettings
from app.db.session import Base
from app.replay.portfolio_engine import PortfolioReplayEngine
from app.research.dataset import load_dataset
from app.research.diagnostics import analyze_dataset
from app.research.v4 import (V3_RESEARCH_END, analyze_oos_records, assert_oos_period, audit_dataset,
                             strategy_config_snapshot)


def write_json(path: Path, payload: dict):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def main():
    data_dir = Path(__file__).resolve().parents[1] / "data"
    config = AppSettings(data_mode="live", auto_scan_enabled=False, strategy_version="v3")
    frozen = strategy_config_snapshot(config)
    v3_meta, v3_frames, _ = load_dataset(data_dir / "v3_canonical.json.gz")
    integrity = audit_dataset(v3_frames, v3_meta["source"])
    integrity["row_count_anomaly_root_cause"] = (
        "V3 metadata used the 15m range as the headline period while rows_by_timeframe included "
        "Yahoo's longer 730d 1h and 5y 1d warm-up histories. Canonical identities are unique; "
        "context rows were not duplicated evaluation rows."
    )
    write_json(data_dir / "v4_data_integrity_report.json", integrity)

    oos_path = data_dir / "v4_oos_canonical.json.gz"
    if not oos_path.exists():
        v3_portfolio_path=data_dir/"v3_portfolio_report.json"
        v3_portfolio=json.loads(v3_portfolio_path.read_text(encoding="utf-8")).get("portfolio_replay",{}) if v3_portfolio_path.exists() else {}
        payload = {"status": "INSUFFICIENT_OOS_DATA", "immutable": True,
            "reason": "Yahoo 15m availability ends at the V3 research boundary; no non-overlapping OOS candles are available.",
            "required_period_start_after": V3_RESEARCH_END.isoformat(), "minimum_trading_days": 60,
            "available_oos_trading_days": 0, "available_oos_evaluation_rows": 0,
            "strategy_config": frozen, "data_integrity": integrity,
            "provider_readiness": {"yahoo": "overlaps_v3", "eodhd_adapter": "ready_requires_api_token",
                "eodhd_token_present": bool(os.getenv("EODHD_API_TOKEN"))},
            "v3_reference_exit_diagnostics": v3_portfolio.get("exit_diagnostics"),
            "sample_confidence": "NO OOS SAMPLE — performance/generalization conclusions prohibited"}
        write_json(data_dir / "v4_oos_report.json", payload)
        print(json.dumps({"output": str(data_dir / "v4_oos_report.json"), "status": payload["status"],
                          "strategy_config_hash": frozen["sha256"]}, indent=2))
        return

    metadata, frames, benchmark = load_dataset(oos_path)
    start = datetime.fromisoformat(metadata["start"]).astimezone(timezone.utc)
    end = datetime.fromisoformat(metadata["end"]).astimezone(timezone.utc)
    assert_oos_period(start, end)
    research = analyze_dataset(frames, config, step=4)
    oos_validation=analyze_oos_records(research["records"],config.entry_score,float(config.min_rr))
    research.pop("records", None)
    engine = create_engine("sqlite:///:memory:"); Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        portfolio = PortfolioReplayEngine(db, config).run(frames, benchmark, 4)
    if portfolio["strategy_config_hash"] != frozen["sha256"]:
        raise RuntimeError("Frozen config hash mismatch")
    payload = {"status": "READY", "immutable": True, "dataset": metadata,
        "strategy_config": frozen, "data_integrity": audit_dataset(frames, metadata["source"]),
        "research": research, "oos_validation":oos_validation,"portfolio_oos": portfolio,
        "sample_confidence": "INSUFFICIENT" if portfolio["trades"] < 10 else "VERY_LOW" if portfolio["trades"] < 30 else "LIMITED" if portfolio["trades"] < 100 else "MORE_USEFUL"}
    write_json(data_dir / "v4_oos_report.json", payload)


if __name__ == "__main__": main()
