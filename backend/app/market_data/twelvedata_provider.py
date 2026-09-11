from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.market_data.provider import DataValidationError, MarketDataProvider, normalize_candles
from app.market_data.symbols import BIST100_SYMBOLS


class TwelveDataProvider(MarketDataProvider):
    """Twelve Data adapter using provider metadata and raw/as-traded OHLC."""
    name = "twelvedata"
    base_url = "https://api.twelvedata.com"
    intervals = {"5m": "5min", "15m": "15min", "1h": "1h", "1d": "1day"}
    price_adjustment = "none"

    def __init__(self, api_key: str, start: datetime | None = None, end: datetime | None = None,
                 client: httpx.Client | None = None):
        if not api_key: raise DataValidationError("TWELVE_DATA_API_KEY gerekli")
        self._api_key, self.start, self.end = api_key, start, end
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": "BIST-Paper-Trading/5.0"})
        self._mapping: dict[str, dict] = {}

    def _request(self, path: str, params: dict) -> dict:
        try:
            response = self.client.get(f"{self.base_url}/{path}", params={**params, "apikey": self._api_key})
            response.raise_for_status(); payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise DataValidationError(f"Twelve Data HTTP {exc.response.status_code}") from None
        except (httpx.HTTPError, ValueError) as exc:
            raise DataValidationError(f"Twelve Data request failed: {type(exc).__name__}") from None
        if payload.get("status") == "error":
            raise DataValidationError(f"Twelve Data error {payload.get('code','unknown')}: {payload.get('message','request rejected')}")
        return payload

    def resolve_symbol(self, symbol: str) -> dict:
        key = symbol.upper().removesuffix(".IS")
        if key in self._mapping: return self._mapping[key]
        payload = self._request("stocks", {"symbol": key, "country": "Turkey"})
        candidates = [row for row in payload.get("data", []) if row.get("symbol", "").upper() == key]
        bist = [row for row in candidates if row.get("mic_code") == "XIST" or "ISTANBUL" in row.get("exchange", "").upper()]
        if not bist: raise DataValidationError(f"{key}: Twelve Data XIST metadata mapping bulunamadı")
        self._mapping[key] = bist[0]
        return bist[0]

    def get_symbols(self) -> list[str]:
        available = {row["symbol"] for row in self.discover_xist_symbols()}
        return [symbol for symbol in BIST100_SYMBOLS if symbol in available]

    def discover_xist_symbols(self) -> list[dict]:
        payload = self._request("stocks", {"country": "Turkey"})
        discovered = []
        for row in payload.get("data", []):
            symbol = row.get("symbol", "").upper().strip()
            is_xist = row.get("mic_code") == "XIST" or "ISTANBUL" in row.get("exchange", "").upper()
            instrument_type = row.get("type", "").lower()
            common_stock = not instrument_type or instrument_type in {"common stock", "common_stock", "stock"}
            if symbol and is_xist and common_stock:
                normalized = dict(row);normalized["symbol"] = symbol
                discovered.append(normalized);self._mapping.setdefault(symbol, normalized)
        return sorted(discovered, key=lambda row: row["symbol"])

    def get_candles(self, symbol: str, timeframe: str, limit: int = 5000):
        timeframe = timeframe.lower()
        if timeframe not in self.intervals: raise DataValidationError(f"Desteklenmeyen timeframe: {timeframe}")
        metadata = self.resolve_symbol(symbol)
        params = {"symbol": metadata["symbol"], "interval": self.intervals[timeframe], "outputsize": min(limit, 5000),
                  "timezone": "UTC" if timeframe != "1d" else "Exchange", "adjust": "none", "order": "ASC"}
        if self.start: params["start_date"] = self.start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if self.end: params["end_date"] = self.end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        payload = self._request("time_series", params)
        meta = payload.get("meta", {})
        timezone_name = "UTC" if timeframe != "1d" else meta.get("exchange_timezone")
        if not timezone_name: raise DataValidationError("Twelve Data timezone metadata eksik")
        try: source_tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError: raise DataValidationError("Twelve Data timezone metadata geçersiz") from None
        rows = []
        for row in payload.get("values", []):
            stamp = datetime.fromisoformat(row["datetime"])
            if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=source_tz)
            rows.append({"timestamp": stamp.astimezone(timezone.utc), "open": Decimal(row["open"]), "high": Decimal(row["high"]),
                         "low": Decimal(row["low"]), "close": Decimal(row["close"]), "volume": Decimal(row.get("volume", 0)), "closed": True})
        candles = normalize_candles(rows, datetime.now(timezone.utc))
        if len(candles) < 35: raise DataValidationError(f"{symbol} {timeframe}: yetersiz kapanmış mum")
        return candles[-limit:]
