from __future__ import annotations

import gzip
import hashlib
import json
import re
from time import perf_counter
from datetime import datetime, timezone
from pathlib import Path

from app.market_data.provider import CandleData
from app.research.dataset import row_to_candle


class HistoricalCandleCache:
    def __init__(self, root: str | Path): self.root = Path(root)

    @staticmethod
    def key(provider: str, symbol: str, timeframe: str, start: datetime | None, end: datetime | None) -> str:
        identity = "|".join(("v2", provider, symbol.upper(), timeframe,
            start.astimezone(timezone.utc).isoformat() if start else "",
            end.astimezone(timezone.utc).isoformat() if end else ""))
        return hashlib.sha256(identity.encode()).hexdigest()

    @staticmethod
    def _rows(candles: list[CandleData]) -> list[dict]:
        return [{"timestamp": candle.timestamp.astimezone(timezone.utc).isoformat(), "open": str(candle.open),
                 "high": str(candle.high), "low": str(candle.low), "close": str(candle.close),
                 "volume": str(candle.volume)} for candle in sorted(candles, key=lambda item: item.timestamp)]

    def get_or_fetch(self, provider: str, symbol: str, timeframe: str, start: datetime | None,
                     end: datetime | None, fetcher) -> tuple[list[CandleData], dict]:
        cache_key = self.key(provider, symbol, timeframe, start, end)
        path = self.root / f"{cache_key}.json.gz"
        if path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as handle: payload = json.load(handle)
            canonical = json.dumps(payload["rows"], sort_keys=True, separators=(",", ":")).encode()
            if hashlib.sha256(canonical).hexdigest() != payload["metadata"]["checksum"]:
                raise ValueError("Historical cache checksum mismatch")
            return [row_to_candle(row) for row in payload["rows"]], payload["metadata"] | {"cache_hit": True}
        started=perf_counter();candles = fetcher();fetch_latency_ms=(perf_counter()-started)*1000;rows = self._rows(candles)
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        metadata = {"provider": provider, "symbol": symbol.upper(), "timeframe": timeframe,
            "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
            "request_start": start.astimezone(timezone.utc).isoformat() if start else None,
            "request_end": end.astimezone(timezone.utc).isoformat() if end else None,
            "row_count": len(rows), "checksum": hashlib.sha256(canonical).hexdigest(),
            "fetch_latency_ms":round(fetch_latency_ms,2)}
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"metadata": metadata, "rows": rows}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as zipped: zipped.write(encoded)
        return candles, metadata | {"cache_hit": False}


def redact_secret(value: str) -> str:
    return re.sub(r"(?i)(apikey|api_key|api_token|access_token|token)=([^&\s]+)",r"\1=***",value)
