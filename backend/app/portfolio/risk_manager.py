from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    quantity: int
    risk_reward: Decimal
    reason: str


def calculate_risk_reward(entry: Decimal, stop: Decimal, target: Decimal) -> Decimal:
    risk = entry - stop
    if risk <= 0:
        return Decimal(0)
    return (target-entry) / risk


def size_position(portfolio_value: Decimal, cash: Decimal, entry: Decimal, stop: Decimal, target: Decimal,
                  risk_pct: Decimal, max_position_pct: Decimal, cash_reserve_pct: Decimal,
                  open_positions: int, max_open_positions: int, min_rr: Decimal, atr_value: Decimal | None = None,
                  minimum_stop_atr_pct: Decimal = Decimal("0.35"), maximum_stop_atr_pct: Decimal = Decimal("4")) -> RiskDecision:
    if entry <= 0 or stop >= entry or target <= entry:
        return RiskDecision(False, 0, Decimal(0), "Geçersiz entry/stop/target")
    rr = calculate_risk_reward(entry, stop, target)
    stop_distance=entry-stop
    if atr_value is not None:
        if atr_value<=0: return RiskDecision(False,0,rr,"ATR geçersiz")
        atr_multiple=stop_distance/atr_value
        if atr_multiple<minimum_stop_atr_pct: return RiskDecision(False,0,rr,f"Stop mesafesi {atr_multiple:.2f} ATR; aşırı küçük")
        if atr_multiple>maximum_stop_atr_pct: return RiskDecision(False,0,rr,f"Stop mesafesi {atr_multiple:.2f} ATR; aşırı uzak")
    if rr < min_rr:
        return RiskDecision(False, 0, rr, f"RR {rr:.2f} < minimum {min_rr}")
    if open_positions >= max_open_positions:
        return RiskDecision(False, 0, rr, "Maksimum açık pozisyon sayısı")
    risk_qty = (portfolio_value*risk_pct/(entry-stop)).to_integral_value(rounding=ROUND_DOWN)
    position_cap_qty = (portfolio_value*max_position_pct/entry).to_integral_value(rounding=ROUND_DOWN)
    spendable = max(Decimal(0), cash-portfolio_value*cash_reserve_pct)
    cash_qty = (spendable/entry).to_integral_value(rounding=ROUND_DOWN)
    quantity = int(min(risk_qty, position_cap_qty, cash_qty))
    if quantity < 1:
        return RiskDecision(False, 0, rr, "Sermaye/risk limiti en az 1 lota izin vermiyor")
    return RiskDecision(True, quantity, rr, "Risk limitleri uygun")
