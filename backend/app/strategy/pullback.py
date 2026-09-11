from decimal import Decimal
from app.market_data.provider import CandleData


def detect_pullback(candles:list[CandleData],trend:dict,structure:dict,support_zone:dict|None,atr_value:Decimal|None,candle_quality:dict)->dict:
    if len(candles)<25 or trend.get("label")!="bullish" or structure.get("label")!="BULLISH" or not support_zone or not atr_value:
        failed=[key for key,value in {"history":len(candles)>=25,"trend":trend.get("label")=="bullish",
            "structure":structure.get("label")=="BULLISH","support_zone":bool(support_zone),"atr":bool(atr_value)}.items() if not value]
        return {"detected":False,"candidate":False,"reason":"Trend/yapı/destek geçmişi yetersiz","quality_score":0,
                "conditions":{},"failed_rules":failed}
    signal=candles[-1]; impulse=max(c.high for c in candles[-21:-1])-min(c.low for c in candles[-21:-1])
    zone_low,zone_high=Decimal(str(support_zone["low"])),Decimal(str(support_zone["high"]))
    touched=signal.low<=zone_high*Decimal("1.01") and signal.close>=zone_low
    reaction=candle_quality.get("bullish") and candle_quality.get("close_location",0)>=Decimal("0.60")
    conditions={"prior_impulse":impulse>=atr_value*Decimal("2"),"support_touch":touched,"reaction":bool(reaction),
                "support_not_broken":signal.close>=zone_low}
    detected=all(conditions.values())
    quality=min(100,int(candle_quality.get("quality_score",0)*Decimal("0.7")+support_zone.get("strength",0)*Decimal("0.3")))
    return {"detected":detected,"trend":"BULLISH","pullback_zone":{"low":zone_low,"high":zone_high},
        "reaction_strength":candle_quality.get("quality_score",0),"invalidation":zone_low-atr_value*Decimal("0.25"),
        "quality_score":quality,"candidate":touched,"conditions":conditions,
        "failed_rules":[key for key,value in conditions.items() if not value],
        "reason":"Trend içi destek tepkisi teyitli" if detected else "Pullback teyidi eksik"}
