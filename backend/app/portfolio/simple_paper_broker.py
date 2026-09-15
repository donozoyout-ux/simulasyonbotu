from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

CENT = Decimal("0.01")


def q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class SimplePaperBroker:
    """Paper-only calculator backed by SimpleStateStore; no ORM or broker API."""

    def __init__(self, store, commission_rate: Decimal, slippage_rate: Decimal, max_position_pct: Decimal = Decimal("0.20")):
        self.store, self.commission_rate, self.slippage_rate = store, Decimal(commission_rate), Decimal(slippage_rate)
        self.max_position_pct = Decimal(max_position_pct)

    def buy(self, symbol: str, price: Decimal, score: int, reason: str, stop: Decimal, target: Decimal,
            now: datetime | None = None) -> dict:
        now = _utc(now); price = Decimal(price)
        def execute(state):
            if state.get("open_position"):
                raise ValueError("simple position already open")
            cash = Decimal(str(state["cash"])); equity = self.equity(state)
            cap = min(equity * self.max_position_pct, cash)
            quantity = int((cap / price).to_integral_value(rounding=ROUND_DOWN))
            if quantity < 1: raise ValueError("insufficient simple paper cash")
            fill = q(price * (1 + self.slippage_rate)); gross = q(fill * quantity)
            commission = q(gross * self.commission_rate); total = gross + commission
            if cash < total: raise ValueError("insufficient simple paper cash")
            position = {"symbol": symbol, "quantity": quantity, "requested_entry_price": float(price),
                "entry_price": float(fill), "current_price": float(fill), "stop": float(stop),
                "target": float(target), "score": score, "reason": reason, "opened_at": now.isoformat(),
                "entry_fees": float(commission), "entry_slippage": float(q((fill-price)*quantity))}
            state["cash"] = float(q(cash-total)); state["open_position"] = position
            state["trades_today"] = int(state.get("trades_today", 0)) + 1
            return dict(position)
        return self.store.mutate(execute, now)

    def mark(self, price: Decimal, now: datetime | None = None) -> dict:
        price = Decimal(price); now = _utc(now)
        def execute(state):
            position = state.get("open_position")
            if not position: raise ValueError("no simple position")
            position["current_price"] = float(price)
            basis = Decimal(str(position["entry_price"])) * position["quantity"] + Decimal(str(position["entry_fees"]))
            pnl = q(price * position["quantity"] - basis)
            return {"symbol": position["symbol"], "price": float(price),
                "current_value": float(q(price*position["quantity"])), "unrealized_pnl": float(pnl),
                "unrealized_pnl_pct": float(pnl / (Decimal(str(position["entry_price"]))*position["quantity"])*100)}
        return self.store.mutate(execute, now)

    def sell(self, price: Decimal, reason: str, now: datetime | None = None) -> dict:
        price = Decimal(price); now = _utc(now)
        def execute(state):
            position = state.get("open_position")
            if not position: raise ValueError("no simple position")
            quantity = int(position["quantity"]); fill = q(price * (1-self.slippage_rate))
            gross = q(fill*quantity); exit_commission = q(gross*self.commission_rate)
            entry_price = Decimal(str(position["entry_price"])); entry_fee = Decimal(str(position["entry_fees"]))
            pnl = q((fill-entry_price)*quantity-exit_commission-entry_fee)
            basis = entry_price*quantity
            trade = {"symbol": position["symbol"], "quantity": quantity, "entry_price": float(entry_price),
                "exit_price": float(fill), "exit_time": now.isoformat(), "realized_pnl": float(pnl),
                "return_pct": float(pnl/basis*100), "commission": float(q(exit_commission+entry_fee)),
                "slippage": float(q(Decimal(str(position.get("entry_slippage", 0)))+(price-fill)*quantity)),
                "reason": reason, "real_order": False}
            state["cash"] = float(q(Decimal(str(state["cash"]))+gross-exit_commission))
            state["realized_pnl"] = float(q(Decimal(str(state.get("realized_pnl", 0)))+pnl))
            state["daily_realized_pnl"] = float(q(Decimal(str(state.get("daily_realized_pnl", 0)))+pnl))
            state.setdefault("last_exit_by_symbol", {})[position["symbol"]] = now.isoformat()
            state.setdefault("daily_trades", []).append(trade)
            state["open_position"] = None
            trade["cash"] = state["cash"]
            return trade
        return self.store.mutate(execute, now)

    @staticmethod
    def equity(state: dict) -> Decimal:
        value = Decimal(str(state.get("cash", 0))); position = state.get("open_position")
        if position: value += Decimal(str(position["current_price"]))*int(position["quantity"])
        return q(value)
