from decimal import Decimal
from app.analysis.indicators import sma


def analyze_volume(volumes: list[Decimal], period: int = 20) -> dict:
    if len(volumes) <= period:
        return {"rvol": None, "volume_expansion": False, "confirmed": False, "quality": "insufficient_data", "complete": False}
    average = sma(volumes[:-1], period)
    baseline = average[-1]
    if baseline <= 0:
        return {"rvol": None, "volume_expansion": False, "confirmed": False, "quality": "invalid_baseline", "complete": False}
    rvol = volumes[-1] / baseline
    quality = "high" if rvol >= Decimal("1.5") else "medium" if rvol >= Decimal("1.25") else "low"
    return {"rvol": rvol, "volume_expansion": rvol > Decimal("1"), "confirmed": rvol >= Decimal("1.25"), "quality": quality, "complete": True, "baseline": baseline}
