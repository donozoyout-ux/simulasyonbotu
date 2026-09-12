from __future__ import annotations

from decimal import Decimal
from time import monotonic

from app.market_data.provider import CandleData, DataValidationError, MarketDataProvider
from app.market_data.twelvedata_provider import TwelveDataProvider
from app.market_data.yahoo_provider import YahooMarketDataProvider


_LIVE_CACHE: dict[tuple[str,str,int],tuple[float,list[CandleData]]] = {}


class HybridMarketDataProvider(MarketDataProvider):
    """
    Production provider:
    - 15m: Twelve Data primary, Yahoo validation/fallback.
    - 1h/1d: Yahoo primary, Twelve Data fallback to conserve Twelve quota.
    - Never falls back to mock data in live mode.
    """

    name = "twelvedata+yahoo"

    def __init__(self, twelve_api_key: str, price_tolerance_pct: Decimal = Decimal("0.015")):
        if not twelve_api_key:
            raise DataValidationError("TWELVE_DATA_API_KEY gerekli")
        self.twelve = TwelveDataProvider(twelve_api_key)
        self.yahoo = YahooMarketDataProvider()
        self.price_tolerance_pct = Decimal(price_tolerance_pct)
        self.last_sources: dict[str, str] = {}
        self.last_validation: dict[str, dict] = {}

    def get_symbols(self) -> list[str]:
        try:
            rows = self.twelve.discover_xist_symbols()
            symbols = [row["symbol"] for row in rows if row.get("symbol")]
            if symbols:
                return list(dict.fromkeys(symbols))
        except Exception:
            pass
        return self.yahoo.get_symbols()

    @staticmethod
    def _last_common(primary: list[CandleData], secondary: list[CandleData]) -> tuple[CandleData, CandleData] | None:
        secondary_by_ts = {item.timestamp: item for item in secondary}
        for item in reversed(primary):
            other = secondary_by_ts.get(item.timestamp)
            if other is not None:
                return item, other
        return None

    def _validate_prices(self, symbol: str, timeframe: str, primary: list[CandleData], secondary: list[CandleData]) -> None:
        pair = self._last_common(primary, secondary)
        if pair is None:
            self.last_validation[f"{symbol}:{timeframe}"] = {
                "status": "NO_COMMON_TIMESTAMP",
                "primary": "twelvedata",
                "secondary": "yahoo",
            }
            return

        left, right = pair
        denominator = max(abs(left.close), abs(right.close), Decimal("0.00000001"))
        divergence = abs(left.close - right.close) / denominator
        payload = {
            "status": "PASS" if divergence <= self.price_tolerance_pct else "DIVERGENCE",
            "timestamp": left.timestamp.isoformat(),
            "twelvedata_close": str(left.close),
            "yahoo_close": str(right.close),
            "divergence_pct": str(divergence * Decimal("100")),
        }
        self.last_validation[f"{symbol}:{timeframe}"] = payload
        if divergence > self.price_tolerance_pct:
            raise DataValidationError(
                f"{symbol} {timeframe}: Twelve/Yahoo close farkı %{(divergence * Decimal('100')):.2f}; "
                f"limit %{(self.price_tolerance_pct * Decimal('100')):.2f}"
            )

    def _fetch_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[CandleData]:
        timeframe = timeframe.lower()

        if timeframe == "5m":
            # Open positions are few (max 4), so Twelve can be primary here without
            # exhausting the free-tier minute quota. Yahoo validates/falls back.
            try:
                primary = self.twelve.get_candles(symbol, timeframe, limit)
                self.last_sources[f"{symbol}:{timeframe}"] = "twelvedata"
            except Exception as twelve_error:
                try:
                    fallback = self.yahoo.get_candles(symbol, timeframe, limit)
                    self.last_sources[f"{symbol}:{timeframe}"] = "yahoo_fallback"
                    self.last_validation[f"{symbol}:{timeframe}"] = {
                        "status": "TWELVE_FAILED",
                        "error": str(twelve_error),
                    }
                    return fallback
                except Exception as yahoo_error:
                    raise DataValidationError(
                        f"{symbol} {timeframe}: Twelve Data ve Yahoo başarısız; "
                        f"Twelve={twelve_error}; Yahoo={yahoo_error}"
                    ) from None

            try:
                secondary = self.yahoo.get_candles(symbol, timeframe, min(max(limit, 40), 80))
                self._validate_prices(symbol, timeframe, primary, secondary)
            except DataValidationError:
                raise
            except Exception as exc:
                self.last_validation[f"{symbol}:{timeframe}"] = {
                    "status": "YAHOO_VALIDATION_UNAVAILABLE",
                    "error": str(exc),
                }
            return primary

        # Bulk 15m scanner + higher timeframes prefer Yahoo. Calling Twelve once
        # per scanned symbol would exceed the free tier (8 API credits/minute).
        # Twelve remains the live 5m source and fallback for scanner timeframes.
        try:
            candles = self.yahoo.get_candles(symbol, timeframe, limit)
            self.last_sources[f"{symbol}:{timeframe}"] = "yahoo"
            return candles
        except Exception as yahoo_error:
            try:
                candles = self.twelve.get_candles(symbol, timeframe, limit)
                self.last_sources[f"{symbol}:{timeframe}"] = "twelvedata_fallback"
                return candles
            except Exception as twelve_error:
                raise DataValidationError(
                    f"{symbol} {timeframe}: Yahoo ve Twelve Data başarısız; "
                    f"Yahoo={yahoo_error}; Twelve={twelve_error}"
                ) from None

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[CandleData]:
        key=(symbol.upper(),timeframe.lower(),limit);cached=_LIVE_CACHE.get(key);now=monotonic()
        if cached and cached[0]>now:return cached[1]
        candles=self._fetch_candles(symbol,timeframe,limit)
        _LIVE_CACHE[key]=(now+(60 if timeframe.lower() in {"5m","15m"} else 300),candles)
        return candles
