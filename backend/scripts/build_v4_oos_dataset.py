import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from app.market_data.eodhd_provider import EodhdHistoricalProvider
from app.research.dataset import liquidity_metrics, save_dataset
from app.research.v4 import assert_oos_period
from scripts.build_v3_dataset import UNIVERSE


# Entirely before V3. These dates are fixed and must not be tuned from outcomes.
OOS_START = datetime(2026, 2, 1, tzinfo=timezone.utc)
OOS_END = datetime(2026, 6, 18, 23, 59, tzinfo=timezone.utc)


def main():
    assert_oos_period(OOS_START, OOS_END)
    token = os.getenv("EODHD_API_TOKEN", "")
    provider = EodhdHistoricalProvider(token, OOS_START, OOS_END)
    frames = {}; errors = []
    def fetch(symbol):
        symbol_frames = {tf: provider.get_candles(symbol, tf, 10000) for tf in ("1d", "1h", "15m")}
        return symbol, symbol_frames, liquidity_metrics(symbol_frames)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, symbol): symbol for symbol in UNIVERSE}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                _, symbol_frames, liquidity = future.result()
                if liquidity["eligible"]: frames[symbol] = symbol_frames
                else: errors.append({"symbol": symbol, "reason": "liquidity_filter"})
            except Exception as exc: errors.append({"symbol": symbol, "reason": str(exc)})
    if len(frames) < 20: raise RuntimeError(f"OOS dataset rejected: only {len(frames)} eligible symbols")
    output = Path(__file__).resolve().parents[1] / "data" / "v4_oos_canonical.json.gz"
    metadata = save_dataset(output, frames, "eodhd", sorted(errors, key=lambda row: row["symbol"]))
    print(json.dumps({"output": str(output), "metadata": metadata}, indent=2))


if __name__ == "__main__": main()
