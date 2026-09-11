from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class UniverseAssessment:
    symbol: str
    status: str
    reason: str
    latest_price: Decimal | None
    affordable: bool


class UniverseBuilder:
    """Provider-owned candidate discovery followed by provider-neutral health gates."""
    def __init__(self,provider,config):self.provider,self.config=provider,config

    def candidates(self,max_symbols:int|None=None)->list[str]:
        symbols=list(dict.fromkeys(self.provider.get_symbols()))
        return symbols[:max_symbols] if max_symbols else symbols

    def assess(self,symbol,frames,portfolio_value:Decimal)->UniverseAssessment:
        if any(not frames.get(timeframe) for timeframe in ("1d","1h","15m")):
            return UniverseAssessment(symbol,"INVALID_DATA","1D/1H/15M history missing",None,False)
        latest=frames["15m"][-1].close
        complete=all(len(frames[timeframe])>=60 for timeframe in ("1d","1h","15m"))
        liquid=sum((candle.volume for candle in frames["15m"][-20:]),Decimal(0))>0
        budget=portfolio_value*self.config.max_position_size_pct
        affordable=latest<=budget
        if not complete:return UniverseAssessment(symbol,"INSUFFICIENT_HISTORY","Minimum history unavailable",latest,affordable)
        if not liquid:return UniverseAssessment(symbol,"ILLIQUID","Recent 15M volume is zero",latest,affordable)
        if not affordable:return UniverseAssessment(symbol,"UNAFFORDABLE","One lot exceeds max-position budget",latest,False)
        return UniverseAssessment(symbol,"TRADABLE","Data, liquidity and affordability passed",latest,True)
