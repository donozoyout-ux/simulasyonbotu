from __future__ import annotations

from datetime import datetime, timezone

from app.market_data.symbols import BIST100_SYMBOLS, BIST30_EXCLUSION_SYMBOLS
from app.research.provider_qualification import candle_quality


OOS_HISTORY_CUTOFF = datetime(2026, 2, 1, tzinfo=timezone.utc)


def select_small_mid_symbols(discovered: list[dict], target: int = 40) -> tuple[list[str], list[str]]:
    available = {row.get("symbol", "").upper() for row in discovered if row.get("symbol")}
    excluded = sorted(available & BIST30_EXCLUSION_SYMBOLS)
    eligible = available - BIST30_EXCLUSION_SYMBOLS
    preferred = [symbol for symbol in BIST100_SYMBOLS if symbol in eligible]
    remainder = sorted(eligible - set(preferred))
    return (preferred + remainder)[:max(20, target)], excluded


def symbol_result(symbol: str, frames: dict, session) -> dict:
    quality = {timeframe: candle_quality(frames.get(timeframe, []), session, timeframe)
               for timeframe in ("15m", "1h", "1d")}
    intraday = frames.get("15m", [])
    latest_price = float(intraday[-1].close) if intraday else None
    earliest = quality["15m"]["earliest"]
    reaches_cutoff = bool(earliest and datetime.fromisoformat(earliest).astimezone(timezone.utc) <= OOS_HISTORY_CUTOFF)
    complete = all(frames.get(timeframe) for timeframe in ("15m", "1h", "1d"))
    good_quality = bool(complete and quality["15m"]["invalid_ohlc_rate_pct"] == 0
                        and quality["15m"]["duplicate_rate_pct"] == 0
                        and quality["15m"]["missing_rate_pct"] is not None
                        and quality["15m"]["missing_rate_pct"] <= 10
                        and quality["15m"]["zero_volume_rate_pct"] is not None
                        and quality["15m"]["zero_volume_rate_pct"] <= 20
                        and quality["15m"]["session_mismatch_rate_pct"] == 0)
    status = ("OOS_CAPABLE" if reaches_cutoff and good_quality else
              "INSUFFICIENT_DATA" if not complete else
              "INSUFFICIENT_HISTORY" if not reaches_cutoff else "ILLIQUID_OR_LOW_QUALITY")
    return {"symbol": symbol, "latest_price": latest_price,
        "15m_earliest": quality["15m"]["earliest"], "15m_latest": quality["15m"]["latest"],
        "15m_rows": quality["15m"]["rows"], "1h_earliest": quality["1h"]["earliest"],
        "1d_earliest": quality["1d"]["earliest"], "missing_15m_pct": quality["15m"]["missing_rate_pct"],
        "zero_volume_pct": quality["15m"]["zero_volume_rate_pct"],
        "invalid_ohlc": quality["15m"]["invalid_ohlc_rate_pct"],
        "session_mismatch": quality["15m"]["session_mismatch_rate_pct"],
        "reaches_2026_02_01": reaches_cutoff, "status": status}


def summarize_small_mid(requested: list[str], rows: list[dict], errors: list[dict], excluded: list[str]) -> dict:
    capable = sorted(row["symbol"] for row in rows if row["status"] == "OOS_CAPABLE")
    valid = sorted(row["symbol"] for row in rows if row["status"] != "INSUFFICIENT_DATA")
    illiquid = sorted(row["symbol"] for row in rows if row["status"] == "ILLIQUID_OR_LOW_QUALITY")
    insufficient_history=sorted(row["symbol"] for row in rows if row["status"] == "INSUFFICIENT_HISTORY")
    failed_symbols = sorted(set(requested) - set(valid))
    status = "FAIL" if len(requested)<20 else "PASS" if len(capable) >= 25 else "PARTIAL"
    return {"status": status, "small_mid_symbols_requested": len(requested), "small_mid_valid": len(valid),
        "15m_oos_capable": len(capable), "failed": len(failed_symbols), "requested_symbols": requested,
        "oos_capable_symbols": capable, "failed_symbols": failed_symbols,
        "illiquid_or_low_quality_symbols": illiquid, "insufficient_history_symbols":insufficient_history,
        "errors": errors, "symbols": sorted(rows,key=lambda row:row["symbol"]),
        "bist30_excluded": excluded, "selection_source": "Twelve Data /stocks XIST discovery; BIST30 conservative exclusion snapshot",
        "quality_rule": "15M starts on/before 2026-02-01; duplicate/invalid/session mismatch 0%; missing <=10%; zero volume <=20%",
        "minimum_oos_capable_required": 25}
