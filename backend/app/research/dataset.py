from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.market_data.provider import CandleData


TIMEFRAMES = ("1d", "1h", "15m")


def candle_to_row(symbol: str, timeframe: str, candle: CandleData, source: str) -> dict:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": candle.timestamp.astimezone(timezone.utc).isoformat(),
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
        "source": source,
        "provider": source,
    }


def row_to_candle(row: dict) -> CandleData:
    return CandleData(
        datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc),
        Decimal(row["open"]), Decimal(row["high"]), Decimal(row["low"]),
        Decimal(row["close"]), Decimal(row["volume"]), True,
    )


def _payload_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def save_dataset(path: str | Path, frames: dict[str, dict[str, list[CandleData]]], source: str,
                 errors: list[dict] | None = None, benchmark: list[CandleData] | None = None,
                 extra_metadata: dict | None = None) -> dict:
    rows = [candle_to_row(symbol, timeframe, candle, source)
            for symbol in sorted(frames)
            for timeframe in TIMEFRAMES
            for candle in sorted(frames[symbol][timeframe], key=lambda item: item.timestamp)]
    duplicate_count = len(rows) - len({(row["symbol"], row["timeframe"], row["timestamp"]) for row in rows})
    invalid_count = sum(Decimal(row["low"]) <= 0 or Decimal(row["high"]) < Decimal(row["low"]) for row in rows)
    timestamps = [datetime.fromisoformat(row["timestamp"]) for row in rows if row["timeframe"] == "15m"]
    trading_days = len({stamp.date() for stamp in timestamps})
    counts = {timeframe: sum(row["timeframe"] == timeframe for row in rows) for timeframe in TIMEFRAMES}
    missing={timeframe:0 for timeframe in TIMEFRAMES}
    for symbol,symbol_frames in frames.items():
        for timeframe in ("15m","1h"):
            daily_counts=Counter(item.timestamp.date() for item in symbol_frames[timeframe])
            if daily_counts:
                modal=Counter(daily_counts.values()).most_common(1)[0][0]
                missing[timeframe]+=sum(max(0,modal-count) for count in daily_counts.values())
    if frames:
        all_daily_dates={item.timestamp.date() for symbol_frames in frames.values() for item in symbol_frames["1d"]}
        missing["1d"]=sum(len(all_daily_dates)-len({item.timestamp.date() for item in symbol_frames["1d"]}) for symbol_frames in frames.values())
    metadata = {
        "source": source,
        "snapshot_at": max(timestamps).isoformat() if timestamps else None,
        "start": min(timestamps).isoformat() if timestamps else None,
        "end": max(timestamps).isoformat() if timestamps else None,
        "trading_days": trading_days,
        "symbols": sorted(frames),
        "timeframes": list(TIMEFRAMES),
        "row_count": len(rows),
        "rows_by_timeframe": counts,
        "missing_candles": missing,
        "duplicates": duplicate_count,
        "invalid_rows": invalid_count,
        "errors": errors or [],
        "survivorship_bias": "Current liquid-symbol universe; historical membership is not reconstructed.",
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    payload = {"metadata": metadata, "rows": rows,
               "benchmark": [candle_to_row("XU100", "1d", item, source) for item in (benchmark or [])]}
    canonical = _payload_bytes(payload)
    metadata["sha256"] = hashlib.sha256(canonical).hexdigest()
    canonical = _payload_bytes(payload)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as zipped:
        zipped.write(canonical)
    return metadata


def load_dataset(path: str | Path) -> tuple[dict, dict[str, dict[str, list[CandleData]]], list[CandleData]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    frames: dict[str, dict[str, list[CandleData]]] = {}
    for row in payload["rows"]:
        frames.setdefault(row["symbol"], {}).setdefault(row["timeframe"], []).append(row_to_candle(row))
    for symbol in frames:
        for timeframe in TIMEFRAMES:
            frames[symbol][timeframe].sort(key=lambda item: item.timestamp)
    benchmark = [row_to_candle(row) for row in payload.get("benchmark", [])]
    return payload["metadata"], frames, benchmark


def liquidity_metrics(frames: dict[str, list[CandleData]]) -> dict:
    daily = frames["1d"][-20:]
    trigger = frames["15m"]
    turnover = sum((item.close * item.volume for item in daily), Decimal(0)) / Decimal(len(daily)) if daily else Decimal(0)
    trading_days = {item.timestamp.date() for item in trigger}
    expected = max(1, len(trading_days) * 32)
    frequency = Decimal(len(trigger)) / Decimal(expected)
    return {"average_turnover_proxy": turnover, "valid_candle_frequency": min(Decimal(1), frequency),
            "eligible": turnover > 0 and frequency >= Decimal("0.80")}
