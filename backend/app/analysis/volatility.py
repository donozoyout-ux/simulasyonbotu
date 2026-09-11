from decimal import Decimal
from app.analysis.indicators import atr


def analyze_volatility(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal]) -> dict:
    value = atr(highs, lows, closes)
    if value is None:
        return {"atr": None, "atr_pct": None, "complete": False}
    return {"atr": value, "atr_pct": value / closes[-1] * 100 if closes[-1] else None, "complete": True}
