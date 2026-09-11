from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.analysis.candle_quality import analyze_candle
from app.analysis.market_structure import analyze_market_structure
from app.analysis.momentum import analyze_momentum
from app.analysis.support_resistance import nearest_zones
from app.analysis.trend import analyze_trend
from app.analysis.volume import analyze_volume
from app.analysis.volatility import analyze_volatility
from app.market_data.market_session import BistMarketSession
from app.market_data.provider import CandleData
from app.portfolio.risk_manager import calculate_risk_reward
from app.strategy.scoring_engine import calculate_score
from app.strategy.setup_engine import detect_setup
from app.strategy.signal_engine import decide


@dataclass(frozen=True)
class PipelineResult:
    symbol: str
    price: Decimal
    score: int
    trend: str
    market_structure: str
    setup: str
    decision: str
    reason: str
    details: dict
    signal_candle_time: datetime
    funnel: dict[str,bool]


def analyze_frames(symbol:str,frames:dict[str,list[CandleData]],config,source:str,analysis_at:datetime|None=None,
                   require_market_session:bool=True)->PipelineResult:
    at=(analysis_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    required=("1d","1h","15m")
    missing=[tf for tf in required if tf not in frames]
    if missing:
        raise ValueError(f"Eksik timeframe: {', '.join(missing)}")
    raw_frames=frames
    frames={tf:[c for c in raw_frames[tf] if c.closed and c.timestamp<=at] for tf in required}
    if any(not frames[tf] for tf in required):
        raise ValueError("Analiz için kapanmış mum bulunamadı")
    ignored_candles={tf:len(raw_frames[tf])-len(frames[tf]) for tf in required}
    daily,hourly,trigger=frames["1d"],frames["1h"],frames["15m"]
    minimum_history=all(len(frames[tf])>=60 for tf in ("1d","1h","15m"))
    data_valid=minimum_history
    session=BistMarketSession.from_config(config)
    freshness_limits={"1d":4320,"1h":240,"15m":config.stale_after_minutes}
    freshness={tf:session.freshness(rows[-1].timestamp,tf,at,freshness_limits[tf]) for tf,rows in frames.items()}
    fresh=all(value=="FRESH" for value in freshness.values())
    source_allowed=source in {"yahoo","eodhd","twelvedata","hybrid","replay"} or config.data_mode=="mock"
    session_valid=(not require_market_session) or session.is_open(at)
    dclose=[c.close for c in daily];hclose=[c.close for c in hourly];tclose=[c.close for c in trigger]
    trend_1d=analyze_trend(dclose);trend_1h=analyze_trend(hclose)
    structure=analyze_market_structure(hourly)
    momentum=analyze_momentum(tclose)
    volume=analyze_volume([c.volume for c in trigger])
    volatility=analyze_volatility([c.high for c in trigger],[c.low for c in trigger],tclose)
    levels=nearest_zones(trigger[-81:-1],tclose[-1])  # signal candle cannot create its own level
    broken_resistances=[zone for zone in levels["zones"] if zone["side"]=="RESISTANCE" and zone["high"]<tclose[-1]
                        and (tclose[-1]/zone["high"]-1)*100<=Decimal("8")]
    levels["breakout_zone"]=max(broken_resistances,key=lambda zone:zone["high"],default=None)
    candle_quality=analyze_candle(trigger[-1],volatility["atr"])
    analysis_complete=all([trend_1d.get("complete"),trend_1h.get("complete"),structure.get("complete"),
                           momentum.get("complete"),volume.get("complete"),volatility.get("complete"),
                           levels.get("support_zone") is not None,levels.get("resistance_zone") is not None])
    setup=detect_setup(trigger,volume,trend_1h,structure,levels,volatility["atr"],candle_quality,
                       require_breakout_volume=config.strategy_version=="v2")
    rr=calculate_risk_reward(setup.entry_area,setup.invalidation_level,setup.target)
    support_strength=(levels.get("support_zone") or {}).get("strength",0)/100
    resistance_strength=(levels.get("resistance_zone") or {}).get("strength",0)/100
    components={"trend":1 if trend_1d["label"]=="bullish" else .45 if trend_1d["label"]=="neutral" else .1,
        "structure":1 if structure["label"]=="BULLISH" else .5 if structure["label"]=="RANGE" else .15,
        "momentum":1 if momentum["label"]=="strong" else .5 if momentum["label"]=="neutral" else .1,
        "volume":min(1,float(volume.get("rvol") or 0)/1.75),"levels":min(1,(support_strength+resistance_strength)/2),
        "setup":setup.score/100,"risk_reward":min(1,float(rr)/2)}
    score,breakdown=calculate_score(components)
    decision,decision_reason=decide(score,config.entry_score,setup.detected,trend_1d["label"],rr,config.min_rr,
        data_valid and fresh,analysis_complete,session_valid,source_allowed,require_bullish_trend=config.strategy_version=="v2",
        reject_bearish_trend=config.strategy_version!="v2")
    funnel={"data_valid":data_valid and fresh,"sufficient_history":minimum_history,"htf_bullish":trend_1d["label"]=="bullish",
        "valid_setup":setup.detected,"score_pass":score>=config.entry_score,"rr_pass":rr>=config.min_rr,
        "session_valid":session_valid,"source_allowed":source_allowed}
    reason=f"1D {trend_1d['label']}; 1H {structure['label']}; 15M {setup.reason}; RVOL {volume.get('rvol')}; {decision_reason}"
    details={"analysis_context":{"daily_candle_time":daily[-1].timestamp,"hourly_candle_time":hourly[-1].timestamp,
        "entry_candle_time":trigger[-1].timestamp,"analysis_at":at,"freshness":freshness},
        "timeframes":{"1d":trend_1d,"1h":trend_1h},"structure":structure,"momentum":momentum,"volume":volume,
        "volatility":volatility,"levels":levels,"candle_quality":candle_quality,"setup":asdict(setup),
        "risk_reward":rr,"score_breakdown":breakdown,"data_stale":not fresh,"analysis_complete":analysis_complete,
        "ignored_open_or_future_candles":ignored_candles}
    return PipelineResult(symbol,tclose[-1],score,trend_1d["label"],structure["label"],setup.setup_type,decision,reason,details,trigger[-1].timestamp,funnel)
