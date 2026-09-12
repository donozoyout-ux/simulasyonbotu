from __future__ import annotations

from sqlalchemy import desc, select

from app.models import DataCollectionActivity


def log_activity(db, module: str, subject: str | None, action: str, status: str, detail: str | None = None):
    row = DataCollectionActivity(
        module=module[:32], subject=(subject or "")[:64] or None, action=action[:32],
        status=status[:24], detail=(detail or "")[:500] or None,
    )
    db.add(row)
    return row


def recent_activity(db, limit: int = 100):
    return db.scalars(select(DataCollectionActivity).order_by(desc(DataCollectionActivity.created_at)).limit(limit)).all()
