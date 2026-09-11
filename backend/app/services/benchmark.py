from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.market_data.provider import DataValidationError


YAHOO_BENCHMARK_SYMBOL = "XU100.IS"


def fetch_xu100_price(timeout: int = 15) -> tuple[Decimal, datetime]:
    """
    Fetch the latest BIST 100 close directly from Yahoo's chart endpoint.

    This intentionally does not require volume because index volume can be empty/zero.
    It is used only for benchmark tracking, never as a trading signal.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO_BENCHMARK_SYMBOL}"
    try:
        response = httpx.get(
            url,
            params={"interval": "5m", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0 BIST-Paper-Trading/1.0"},
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0].get("close") or []
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise DataValidationError(f"XU100 benchmark request failed: {type(exc).__name__}") from None

    for ts, close in reversed(list(zip(timestamps, closes))):
        if close is None:
            continue
        try:
            price = Decimal(str(close))
        except Exception:
            continue
        if price <= 0:
            continue
        return price, datetime.fromtimestamp(int(ts), timezone.utc)

    raise DataValidationError("XU100 benchmark close not found")
