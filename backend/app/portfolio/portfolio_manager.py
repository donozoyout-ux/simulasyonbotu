from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Portfolio, PortfolioSnapshot, Position


def ensure_portfolio(db: Session, initial_balance: Decimal) -> Portfolio:
    portfolio = db.get(Portfolio, 1)
    if not portfolio:
        portfolio = Portfolio(id=1, initial_balance=initial_balance, cash_balance=initial_balance, realized_pnl=Decimal(0))
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
    return portfolio


def portfolio_summary(db: Session, initial_balance: Decimal) -> dict:
    portfolio = ensure_portfolio(db, initial_balance)
    positions = db.scalars(select(Position).where(Position.status == "OPEN")).all()
    invested = sum((p.current_price * p.quantity for p in positions), Decimal(0))
    cost_basis = sum((p.entry_price * p.quantity + p.entry_fees for p in positions), Decimal(0))
    unrealized = invested - cost_basis
    value = portfolio.cash_balance + invested
    total_pnl = value - portfolio.initial_balance
    return {"initial_balance": portfolio.initial_balance, "cash_balance": portfolio.cash_balance,
            "invested_value": invested, "portfolio_value": value, "realized_pnl": portfolio.realized_pnl,
            "unrealized_pnl": unrealized, "total_pnl": total_pnl,
            "total_return_pct": total_pnl / portfolio.initial_balance * 100 if portfolio.initial_balance else Decimal(0),
            "open_positions": len(positions)}


def take_snapshot(db: Session, initial_balance: Decimal, run_id: str | None = None) -> PortfolioSnapshot:
    summary = portfolio_summary(db, initial_balance)
    snapshot = PortfolioSnapshot(cash=summary["cash_balance"], invested_value=summary["invested_value"],
        portfolio_value=summary["portfolio_value"], realized_pnl=summary["realized_pnl"], unrealized_pnl=summary["unrealized_pnl"],run_id=run_id)
    db.add(snapshot)
    db.commit()
    return snapshot
