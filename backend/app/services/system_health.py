from __future__ import annotations


def scan_data_status(run) -> str:
    if not run:
        return "NO_SCAN"
    if not run.failed_symbols and not run.stale_symbols:
        return "OK"
    if run.failed_symbols and (run.valid_symbols == 0 or run.failed_symbols > run.total_symbols / 2):
        return "DATA_ERROR"
    return "PARTIAL"


def classify_data_health(run, market_open: bool) -> dict[str, str]:
    status = scan_data_status(run)
    analysis_mode = getattr(run, "analysis_mode", "LIVE") if run else "LIVE"
    if status in {"NO_SCAN", "OK"}:
        return {"status": status, "severity": "OK", "system_status": "LIVE_PAPER"}
    if status == "PARTIAL":
        return {"status": status, "severity": "WARNING", "system_status": "DATA_DEGRADED"}
    if analysis_mode == "ANALYSIS_ONLY" or not market_open:
        return {"status": status, "severity": "WARNING", "system_status": "DATA_DEGRADED"}
    return {"status": status, "severity": "CRITICAL", "system_status": "DATA_ERROR"}
