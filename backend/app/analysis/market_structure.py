from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from app.market_data.provider import CandleData


@dataclass(frozen=True)
class Swing:
    index: int
    timestamp: datetime | None
    price: Decimal
    type: str
    strength: Decimal


def detect_swings(highs: list[Decimal], lows: list[Decimal], timestamps: list[datetime] | None = None, window: int = 2) -> list[Swing]:
    if len(highs) != len(lows) or (timestamps is not None and len(timestamps) != len(highs)):
        raise ValueError("Swing input uzunlukları eşit olmalı")
    swings: list[Swing] = []
    for index in range(window, len(highs) - window):
        neighborhood_high = highs[index-window:index+window+1]
        neighborhood_low = lows[index-window:index+window+1]
        ts = timestamps[index] if timestamps else None
        if highs[index] == max(neighborhood_high) and neighborhood_high.count(highs[index]) == 1:
            baseline = min(lows[index-window:index+window+1])
            swings.append(Swing(index, ts, highs[index], "SWING_HIGH", (highs[index]-baseline)/highs[index]*100))
        if lows[index] == min(neighborhood_low) and neighborhood_low.count(lows[index]) == 1:
            baseline = max(highs[index-window:index+window+1])
            swings.append(Swing(index, ts, lows[index], "SWING_LOW", (baseline-lows[index])/lows[index]*100))
    return sorted(swings, key=lambda item: item.index)


def swing_points(highs: list[Decimal], lows: list[Decimal], window: int = 2):
    swings = detect_swings(highs, lows, window=window)
    return ([(s.index, s.price) for s in swings if s.type == "SWING_HIGH"],
            [(s.index, s.price) for s in swings if s.type == "SWING_LOW"])


def analyze_market_structure(candles: list[CandleData], window: int = 2) -> dict:
    if len(candles) < max(12, window*4+4):
        return {"label": "UNCERTAIN", "complete": False, "swings": [], "sequence": []}
    swings = detect_swings([c.high for c in candles], [c.low for c in candles], [c.timestamp for c in candles], window)
    highs = [s for s in swings if s.type == "SWING_HIGH"]
    lows = [s for s in swings if s.type == "SWING_LOW"]
    if len(highs) < 2 or len(lows) < 2:
        return {"label": "UNCERTAIN", "complete": False, "swings": [asdict(s) for s in swings], "sequence": []}
    sequence = ["HH" if highs[-1].price > highs[-2].price else "LH", "HL" if lows[-1].price > lows[-2].price else "LL"]
    if sequence == ["HH", "HL"]: label = "BULLISH"
    elif sequence == ["LH", "LL"]: label = "BEARISH"
    elif abs(highs[-1].price-highs[-2].price)/highs[-2].price <= Decimal("0.01") and abs(lows[-1].price-lows[-2].price)/lows[-2].price <= Decimal("0.01"): label = "RANGE"
    else: label = "UNCERTAIN"
    return {"label": label, "complete": True, "swings": [asdict(s) for s in swings[-12:]], "sequence": sequence}


def classify_structure(highs: list[Decimal], lows: list[Decimal]) -> str:
    sh, sl = swing_points(highs, lows)
    if len(sh) < 2 or len(sl) < 2: return "uncertain"
    if sh[-1][1] > sh[-2][1] and sl[-1][1] > sl[-2][1]: return "bullish_hh_hl"
    if sh[-1][1] < sh[-2][1] and sl[-1][1] < sl[-2][1]: return "bearish_lh_ll"
    return "range" if abs(sh[-1][1]-sh[-2][1])/sh[-2][1] <= Decimal("0.01") else "uncertain"

