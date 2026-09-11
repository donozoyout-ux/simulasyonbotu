from decimal import Decimal
from app.analysis.indicators import macd, rate_of_change, rsi


def analyze_momentum(closes: list[Decimal]) -> dict:
    rsi_values = rsi(closes)
    rsi_value = rsi_values[-1] if rsi_values else None
    macd_value = macd(closes)
    roc = rate_of_change(closes)
    if rsi_value is None or macd_value is None or roc is None:
        return {"label": "insufficient_data", "rsi": rsi_value, "macd_histogram": None, "roc": roc, "complete": False}
    if rsi_value >= 55 and macd_value["histogram"] > 0 and roc > 0:
        label = "strong"
    elif rsi_value < 45 and macd_value["histogram"] < 0:
        label = "weak"
    else:
        label = "neutral"
    return {"label": label, "rsi": rsi_value, "macd_histogram": macd_value["histogram"], "roc": roc, "complete": True}
