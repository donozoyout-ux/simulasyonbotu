from decimal import Decimal
from app.market_data.provider import CandleData


def analyze_candle(candle: CandleData, atr_value: Decimal | None) -> dict:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return {"complete": False, "quality_score": 0, "reason": "zero_range"}
    body = abs(candle.close-candle.open)
    upper_wick = candle.high-max(candle.open,candle.close)
    lower_wick = min(candle.open,candle.close)-candle.low
    body_pct = body/candle_range
    close_location = (candle.close-candle.low)/candle_range
    atr_relative = candle_range/atr_value if atr_value and atr_value > 0 else None
    score = 100
    if body_pct < Decimal("0.20"): score -= 45
    elif body_pct < Decimal("0.45"): score -= 20
    if upper_wick/candle_range > Decimal("0.35"): score -= 25
    if candle.close <= candle.open: score -= 30
    if close_location < Decimal("0.65"): score -= 15
    if atr_relative is not None and atr_relative > Decimal("2.5"): score -= 15
    return {"complete": True,"body_pct":body_pct,"upper_wick_pct":upper_wick/candle_range,
            "lower_wick_pct":lower_wick/candle_range,"range":candle_range,"atr_relative_range":atr_relative,
            "close_location":close_location,"quality_score":max(0,score),"bullish":candle.close>candle.open}

