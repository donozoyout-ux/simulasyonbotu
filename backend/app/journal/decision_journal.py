from sqlalchemy.orm import Session
from app.models import DecisionLog


def log_decision(db: Session, category: str, decision: str, reason: str, symbol: str | None = None, score: int | None = None,
                 details: dict | None = None, run_id: str | None = None, strategy_version: str | None = None,
                 strategy_config_hash: str | None = None):
    entry = DecisionLog(symbol=symbol, category=category, decision=decision, reason=reason, score=score, details=details or {},
        run_id=run_id,strategy_version=strategy_version,strategy_config_hash=strategy_config_hash)
    db.add(entry); db.commit(); return entry
