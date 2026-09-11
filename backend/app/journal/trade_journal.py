from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Trade


def list_trades(db: Session, limit: int = 100):
    return list(db.scalars(select(Trade).order_by(Trade.exit_time.desc()).limit(limit)).all())

