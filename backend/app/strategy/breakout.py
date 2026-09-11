from decimal import Decimal
from app.market_data.provider import CandleData


def detect_breakout(candles: list[CandleData], resistance_zone: dict | None, volume: dict, candle_quality: dict,
                    require_volume: bool = True) -> dict:
    if len(candles) < 2 or not resistance_zone:
        return {"detected":False,"direction":None,"reason":"Önceki direnç zone bulunamadı","quality_score":0}
    signal, previous = candles[-1], candles[-2]
    level=Decimal(str(resistance_zone["high"]))
    breakout_pct=(signal.close/level-1)*100 if level else Decimal(0)
    conditions={"prior_below":previous.close<=level,"closed_above":signal.closed and signal.close>level,
        "meaningful_body":candle_quality.get("body_pct",0)>=Decimal("0.45"),
        "wick_ok":candle_quality.get("upper_wick_pct",1)<=Decimal("0.35"),
        "volume_ok":bool(volume.get("confirmed")) or not require_volume,"not_extended":breakout_pct<=Decimal("8")}
    detected=all(conditions.values())
    quality=min(100,int(candle_quality.get("quality_score",0)*Decimal("0.55") + min(Decimal(100),(volume.get("rvol") or 0)*50)*Decimal("0.45")))
    failed=[key for key,value in conditions.items() if not value]
    return {"detected":detected,"direction":"UP" if detected else None,"level":level,"close":signal.close,
            "breakout_pct":breakout_pct,"volume_ratio":volume.get("rvol"),"quality_score":quality,
            "candidate":conditions["prior_below"] and conditions["closed_above"],"conditions":conditions,"failed_rules":failed,
            "reason":"Direnç zone üzerinde kaliteli kapanış" if detected else "Breakout reddedildi: "+", ".join(failed)}
