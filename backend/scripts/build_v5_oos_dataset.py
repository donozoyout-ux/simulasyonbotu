import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from app.market_data.cache import HistoricalCandleCache, redact_secret
from app.market_data.eodhd_provider import EodhdHistoricalProvider
from app.market_data.twelvedata_provider import TwelveDataProvider
from app.research.dataset import liquidity_metrics, save_dataset
from app.research.v4 import assert_oos_period
from scripts.build_v3_dataset import UNIVERSE


WARMUP_START = datetime(2025, 11, 1, tzinfo=timezone.utc)
EVALUATION_START = datetime(2026, 2, 1, tzinfo=timezone.utc)
EVALUATION_END = datetime(2026, 5, 31, 23, 59, tzinfo=timezone.utc)


def _provider(name: str):
    if name == "eodhd":
        return EodhdHistoricalProvider(os.getenv("EODHD_API_TOKEN", ""), WARMUP_START, EVALUATION_END)
    if name == "twelvedata":
        return TwelveDataProvider(os.getenv("TWELVE_DATA_API_KEY", ""), WARMUP_START, EVALUATION_END)
    raise RuntimeError(f"Qualified provider is unsupported for fixed V5 OOS: {name}")


def main():
    assert_oos_period(EVALUATION_START, EVALUATION_END)
    data_dir = Path(__file__).resolve().parents[1] / "data"
    qualification_path = data_dir / "v5_provider_qualification.json"
    if not qualification_path.exists():
        raise RuntimeError("Run python -m scripts.qualify_provider first")
    qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
    if qualification.get("replay_gate",{}).get("status") != "PASS":
        raise RuntimeError("QUALIFICATION_INCOMPLETE: Twelve Data small/mid qualification has not passed")
    name = qualification.get("selected_primary")
    if not name:
        raise RuntimeError("WAITING_FOR_PROVIDER_CREDENTIAL: no provider passed qualification")
    if name == "twelvedata" and qualification.get("twelvedata_small_mid",{}).get("status") != "PASS":
        raise RuntimeError("QUALIFICATION_INCOMPLETE: Twelve Data small/mid universe has fewer than 25 OOS-capable symbols")
    provider = _provider(name)
    cache = HistoricalCandleCache(data_dir / "provider_cache")
    universe=(qualification["twelvedata_small_mid"]["oos_capable_symbols"] if name=="twelvedata" else UNIVERSE)
    frames, errors, cache_records = {}, [], []

    def fetch(symbol):
        symbol_frames = {}
        metadata = []
        for timeframe in ("1d", "1h", "15m"):
            candles, cache_meta = cache.get_or_fetch(
                name, symbol, timeframe, WARMUP_START, EVALUATION_END,
                lambda timeframe=timeframe: provider.get_candles(symbol, timeframe, 10000),
            )
            symbol_frames[timeframe] = candles
            metadata.append(cache_meta)
        return symbol, symbol_frames, liquidity_metrics(symbol_frames), metadata

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, symbol): symbol for symbol in universe}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                _, symbol_frames, liquidity, metadata = future.result()
                cache_records.extend(metadata)
                if liquidity["eligible"]:
                    frames[symbol] = symbol_frames
                else:
                    errors.append({"symbol": symbol, "reason": "liquidity_filter"})
            except Exception as exc:
                errors.append({"symbol": symbol, "reason": redact_secret(str(exc))})
    if not frames:
        raise RuntimeError("OOS dataset rejected: no eligible symbols")
    oos_quality="PASS" if len(frames)>=25 else "PARTIAL"

    evaluation_rows = {timeframe: sum(
        EVALUATION_START <= candle.timestamp <= EVALUATION_END
        for symbol_frames in frames.values() for candle in symbol_frames[timeframe]
    ) for timeframe in ("15m", "1h", "1d")}
    warmup_rows = {timeframe: sum(
        candle.timestamp < EVALUATION_START
        for symbol_frames in frames.values() for candle in symbol_frames[timeframe]
    ) for timeframe in ("15m", "1h", "1d")}
    trading_days = len({
        candle.timestamp.date() for symbol_frames in frames.values() for candle in symbol_frames["15m"]
        if EVALUATION_START <= candle.timestamp <= EVALUATION_END
    })
    output = data_dir / "v5_oos_canonical.json.gz"
    metadata = save_dataset(
        output, frames, name, sorted(errors, key=lambda row: row["symbol"]),
        extra_metadata={
            "provider": name,
            "oos_quality":oos_quality,
            "requested_symbols":len(universe),
            "valid_symbols":len(frames),
            "failed_symbols":sorted(set(universe)-set(frames)),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "warmup_start": WARMUP_START.isoformat(),
            "evaluation_start": EVALUATION_START.isoformat(),
            "evaluation_end": EVALUATION_END.isoformat(),
            "trading_days": trading_days,
            "warmup_rows": warmup_rows,
            "evaluation_rows": evaluation_rows,
            "cache_checksums": sorted({row["checksum"] for row in cache_records}),
        },
    )
    print(json.dumps({"output": str(output), "metadata": metadata}, indent=2))


if __name__ == "__main__":
    main()
