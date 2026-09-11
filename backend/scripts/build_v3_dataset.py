import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.market_data.yahoo_provider import YahooMarketDataProvider
from app.research.dataset import liquidity_metrics, save_dataset


UNIVERSE = [
    "AKBNK", "ARCLK", "ASELS", "BIMAS", "CCOLA", "CIMSA", "DOAS", "EKGYO", "ENKAI", "EREGL",
    "FROTO", "GARAN", "GUBRF", "HALKB", "ISCTR", "KCHOL", "KRDMD", "MAVI", "MGROS", "PETKM",
    "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO", "TKFEN", "TOASO", "TSKB",
    "TTKOM", "TUPRS", "ULKER", "VAKBN", "YKBNK",
]


def fetch(symbol):
    provider = YahooMarketDataProvider()
    frames = {timeframe: provider.get_candles(symbol, timeframe, 10000) for timeframe in ("1d", "1h", "15m")}
    return symbol, frames, liquidity_metrics(frames)


def main():
    frames = {}; errors = []; liquidity = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch, symbol): symbol for symbol in UNIVERSE}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                _, symbol_frames, metrics = future.result()
                liquidity[symbol] = {key: float(value) if hasattr(value, "as_tuple") else value for key, value in metrics.items()}
                if metrics["eligible"]:
                    frames[symbol] = symbol_frames
                else:
                    errors.append({"symbol": symbol, "reason": "liquidity_or_frequency_filter", "metrics": liquidity[symbol]})
            except Exception as exc:
                errors.append({"symbol": symbol, "reason": str(exc)})
    provider = YahooMarketDataProvider()
    try:
        benchmark = provider.get_candles("XU100", "1d", 10000)
    except Exception as exc:
        benchmark = [];errors.append({"symbol": "XU100", "reason": str(exc)})
    path = Path(__file__).resolve().parents[1] / "data" / "v3_canonical.json.gz"
    metadata = save_dataset(path, frames, "yahoo", sorted(errors, key=lambda item: item["symbol"]), benchmark)
    print(json.dumps({"path": str(path), "metadata": metadata, "liquidity": liquidity}, indent=2))


if __name__ == "__main__":
    main()
