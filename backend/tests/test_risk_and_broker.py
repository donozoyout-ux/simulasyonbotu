from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.session import Base
from app.models import Portfolio
from app.portfolio.paper_broker import PaperBroker
from app.portfolio.risk_manager import calculate_risk_reward, size_position


def test_position_sizing_clamps_to_twenty_percent():
    result = size_position(Decimal("5000"), Decimal("5000"), Decimal("100"), Decimal("95"), Decimal("110"),
        Decimal("0.005"), Decimal("0.20"), Decimal("0.10"), 0, 4, Decimal("1.5"))
    assert result.approved and result.quantity == 5  # risk amount 25 / stop distance 5
    assert result.risk_reward == Decimal("2")


def test_rejects_low_rr_and_too_small_capital():
    assert not size_position(Decimal("5000"), Decimal("5000"), Decimal("100"), Decimal("95"), Decimal("105"),
        Decimal(".005"), Decimal(".2"), Decimal(".1"), 0, 4, Decimal("1.5")).approved
    assert calculate_risk_reward(Decimal("100"), Decimal("100"), Decimal("110")) == 0


def test_buy_sell_fees_slippage_and_accounting():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        portfolio = Portfolio(id=1, initial_balance=Decimal("5000"), cash_balance=Decimal("5000"), realized_pnl=Decimal(0))
        db.add(portfolio); db.commit()
        broker = PaperBroker(db, Decimal(".001"), Decimal(".0005"))
        position = broker.buy(portfolio, "ASELS", 4, Decimal("100"), Decimal("95"), Decimal("110"), "BREAKOUT", 88, "test")
        assert position.entry_price == Decimal("100.05")
        assert portfolio.cash_balance == Decimal("4599.40")
        trade = broker.sell(portfolio, position, 4, Decimal("110"), "target")
        assert trade.exit_price == Decimal("109.95")
        assert trade.realized_pnl == Decimal("38.76")
        assert portfolio.cash_balance == Decimal("5038.76")
