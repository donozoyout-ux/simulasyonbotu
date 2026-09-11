from decimal import Decimal
from app.analysis.indicators import ema


def analyze_trend(closes: list[Decimal]) -> dict:
    e20, e50 = ema(closes, 20), ema(closes, 50)
    if not e20 or not e50:
        return {"label": "insufficient_data", "strength": None, "ema20": None, "ema50": None, "complete": False}
    price, fast, slow = closes[-1], e20[-1], e50[-1]
    if price > fast > slow:
        label = "bullish"
    elif price < fast < slow:
        label = "bearish"
    else:
        label = "neutral"
    strength = min(100, int(abs((fast/slow-1)*100)*12)) if slow else 0
    return {"label": label, "strength": strength, "ema20": fast, "ema50": slow, "complete": True}
