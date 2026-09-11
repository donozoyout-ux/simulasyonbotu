from dataclasses import asdict, dataclass
from decimal import Decimal

from app.analysis.market_structure import detect_swings
from app.market_data.provider import CandleData


@dataclass(frozen=True)
class PriceZone:
    low: Decimal
    high: Decimal
    center: Decimal
    touch_count: int
    strength: int
    last_touch_index: int
    side: str


def build_zones(candles: list[CandleData], tolerance_pct: Decimal = Decimal("0.008")) -> list[PriceZone]:
    if len(candles) < 12:
        return []
    swings = detect_swings([c.high for c in candles], [c.low for c in candles], [c.timestamp for c in candles])
    clusters: list[list] = []
    for swing in swings:
        match = next((cluster for cluster in clusters if abs(swing.price-cluster[0].price)/cluster[0].price <= tolerance_pct), None)
        if match is None: clusters.append([swing])
        else: match.append(swing)
    zones=[]
    for cluster in clusters:
        prices=[s.price for s in cluster]; center=sum(prices)/Decimal(len(prices)); last=max(s.index for s in cluster)
        volume_ratio=sum((candles[s.index].volume for s in cluster),Decimal(0))/Decimal(len(cluster))
        all_volume=sum((c.volume for c in candles),Decimal(0))/Decimal(len(candles))
        recency=max(0,20-(len(candles)-1-last))
        strength=min(100,len(cluster)*20+recency+int(min(Decimal(20),(volume_ratio/all_volume if all_volume else 0)*10)))
        side="RESISTANCE" if sum(1 for s in cluster if s.type=="SWING_HIGH") >= len(cluster)/2 else "SUPPORT"
        zones.append(PriceZone(min(prices),max(prices),center,len(cluster),strength,last,side))
    return sorted(zones,key=lambda z:(z.center,z.strength))


def nearest_zones(candles: list[CandleData], price: Decimal) -> dict:
    zones=build_zones(candles)
    supports=[z for z in zones if z.center < price and z.side=="SUPPORT"]
    resistances=[z for z in zones if z.center > price and z.side=="RESISTANCE"]
    support=max(supports,key=lambda z:z.center,default=None)
    resistance=min(resistances,key=lambda z:z.center,default=None)
    return {"support_zone": asdict(support) if support else None,"resistance_zone":asdict(resistance) if resistance else None,
            "support":support.center if support else None,"resistance":resistance.center if resistance else None,
            "zones":[asdict(z) for z in zones]}


def find_levels(highs: list[Decimal], lows: list[Decimal], price: Decimal, lookback: int = 60) -> dict:
    from datetime import datetime, timedelta, timezone
    start=max(0,len(highs)-lookback)
    candles=[CandleData(datetime.now(timezone.utc)-timedelta(minutes=15*(len(highs)-i)),price,highs[i],lows[i],price,Decimal(1)) for i in range(start,len(highs))]
    return nearest_zones(candles,price)

