from dataclasses import dataclass
from decimal import Decimal

from app.market_data.provider import CandleData
from app.strategy.breakout import detect_breakout
from app.strategy.pullback import detect_pullback


@dataclass(frozen=True)
class SetupResult:
    detected: bool
    setup_type: str
    score: int
    reason: str
    risk_level: str
    entry_area: Decimal
    invalidation_level: Decimal
    target: Decimal
    evidence: dict


def no_setup(price:Decimal,reason:str,evidence:dict|None=None,quality_score:int=0)->SetupResult:
    return SetupResult(False,"NONE",quality_score,reason,"high",price,price,price,evidence or {})


def structural_target(price:Decimal,levels:dict,atr_value:Decimal)->tuple[Decimal,str]:
    candidates=[Decimal(str(zone["low"])) for zone in levels.get("zones",[]) if zone["side"]=="RESISTANCE" and Decimal(str(zone["low"]))>price]
    if candidates:return min(candidates),"next_resistance_zone"
    return price+atr_value*Decimal("3"),"atr_projection_no_resistance"


def detect_setup(candles:list[CandleData],volume:dict,trend:dict,structure:dict,levels:dict,
                 atr_value:Decimal|None,candle_quality:dict,require_breakout_volume:bool=True)->SetupResult:
    if len(candles)<25 or not atr_value or not volume.get("complete") or not trend.get("complete") or not structure.get("complete"):
        return no_setup(candles[-1].close,"Analiz geçmişi eksik")
    price=candles[-1].close
    breakout_zone=levels.get("resistance_zone") if require_breakout_volume else levels.get("breakout_zone")
    breakout=detect_breakout(candles,breakout_zone,volume,candle_quality,require_breakout_volume)
    pullback=detect_pullback(candles,trend,structure,levels.get("support_zone"),atr_value,candle_quality)
    support=levels.get("support_zone")
    support_conditions={"support_zone":bool(support),"trend":trend.get("label")=="bullish",
                        "prior_not_broken":False,"touched":False,"reaction":False}
    if support:
        support_low,support_high=Decimal(str(support["low"])),Decimal(str(support["high"]))
        support_conditions.update({"prior_not_broken":candles[-2].close>=support_low and candles[-1].close>=support_low,
            "touched":candles[-1].low<=support_high*Decimal("1.005"),
            "reaction":bool(candle_quality.get("bullish") and candle_quality.get("quality_score",0)>=60)})
    recent=candles[-6:-1]
    continuation_conditions={"trend":trend.get("label")=="bullish","structure":structure.get("label")=="BULLISH",
        "controlled":(max(c.high for c in recent)-min(c.low for c in recent))<=atr_value*Decimal("3"),
        "re_expansion":bool(candle_quality.get("bullish") and candle_quality.get("quality_score",0)>=65 and volume.get("volume_expansion"))}
    diagnostics={"BREAKOUT":breakout,"PULLBACK":pullback,
        "SUPPORT_BOUNCE":{"detected":all(support_conditions.values()),"candidate":support_conditions["touched"],
            "conditions":support_conditions,"failed_rules":[key for key,value in support_conditions.items() if not value],
            "quality_score":min(100,round(candle_quality.get("quality_score",0)*.7+(support or {}).get("strength",0)*.3))},
        "TREND_CONTINUATION":{"detected":all(continuation_conditions.values()),"candidate":continuation_conditions["controlled"],
            "conditions":continuation_conditions,"failed_rules":[key for key,value in continuation_conditions.items() if not value],
            "quality_score":round(candle_quality.get("quality_score",0)*.6+min(100,float(volume.get("rvol") or 0)*50)*.2+
                                  (100 if structure.get("label")=="BULLISH" else 30)*.2)}}
    target,target_source=structural_target(price,levels,atr_value)
    if trend.get("label")=="bullish" and structure.get("label")=="BULLISH" and breakout["detected"]:
        stop=max(Decimal(str(breakout["level"]))-atr_value,Decimal(str(levels.get("support") or price-atr_value*2)))
        chosen_target=price+(price-stop)*Decimal("2") if require_breakout_volume else target
        return SetupResult(True,"BREAKOUT",breakout["quality_score"],breakout["reason"],"medium",price,stop,chosen_target,{"selected":breakout,"diagnostics":diagnostics,"target_source":"fixed_2r_legacy" if require_breakout_volume else target_source})
    if pullback["detected"]:
        stop=Decimal(str(pullback["invalidation"]))
        chosen_target=price+(price-stop)*Decimal("1.8") if require_breakout_volume else target
        return SetupResult(True,"PULLBACK",pullback["quality_score"],pullback["reason"],"medium",price,stop,chosen_target,{"selected":pullback,"diagnostics":diagnostics,"target_source":"fixed_1.8r_legacy" if require_breakout_volume else target_source})
    if support:
        low,high=Decimal(str(support["low"])),Decimal(str(support["high"]))
        prior_not_broken=candles[-2].close>=low and candles[-1].close>=low
        touched=candles[-1].low<=high*Decimal("1.005")
        reaction=candle_quality.get("bullish") and candle_quality.get("quality_score",0)>=60
        if trend.get("label")=="bullish" and prior_not_broken and touched and reaction:
            stop=low-atr_value*Decimal("0.25")
            quality=min(100,round(candle_quality["quality_score"]*.7+support["strength"]*.3))
            chosen_target=price+(price-stop)*Decimal("1.8") if require_breakout_volume else target
            return SetupResult(True,"SUPPORT_BOUNCE",quality,"Kırılmamış destek zone üzerinde teyitli reaction candle","low",price,stop,chosen_target,{"selected":{"zone":support,"candle_quality":candle_quality},"diagnostics":diagnostics,"target_source":"fixed_1.8r_legacy" if require_breakout_volume else target_source})
    controlled=(max(c.high for c in recent)-min(c.low for c in recent))<=atr_value*Decimal("3")
    re_expansion=candle_quality.get("bullish") and candle_quality.get("quality_score",0)>=65 and volume.get("volume_expansion")
    if trend.get("label")=="bullish" and structure.get("label")=="BULLISH" and controlled and re_expansion:
        stop=Decimal(str(levels.get("support") or price-atr_value*Decimal("1.5")))
        chosen_target=price+(price-stop)*Decimal("1.8") if require_breakout_volume else target
        quality=70 if require_breakout_volume else diagnostics["TREND_CONTINUATION"]["quality_score"]
        return SetupResult(True,"TREND_CONTINUATION",quality,"HTF trend + bullish structure + kontrollü konsolidasyon + momentum genişlemesi","medium",price,stop,chosen_target,{"selected":{"controlled":True,"re_expansion":True},"diagnostics":diagnostics,"target_source":"fixed_1.8r_legacy" if require_breakout_volume else target_source})
    candidate_quality=0 if require_breakout_volume else max((item.get("quality_score",0) for item in diagnostics.values() if item.get("candidate")),default=0)
    return no_setup(price,"Teyitli setup yok",{"diagnostics":diagnostics},candidate_quality)
