from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Position


def open_positions(db: Session) -> list[Position]:
    return list(db.scalars(select(Position).where(Position.status == "OPEN")).all())


def mark_price(db: Session, symbol: str, price: Decimal) -> None:
    for position in db.scalars(select(Position).where(Position.symbol == symbol, Position.status == "OPEN")):
        position.current_price = price
    db.commit()

