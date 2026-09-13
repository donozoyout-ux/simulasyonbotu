from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import WatchlistItem


def update_watchlist(db: Session, symbol: str, score: int, setup: str, reason: str, threshold: int, entry_threshold: int,
                     data_valid: bool = True, analysis_complete: bool = True, minimum_history: bool = True, liquidity_valid: bool = True,
                     price=None,setup_quality=None,trend=None,structure=None,support=None,resistance=None,rr=None,run_id=None,analysis_mode="LIVE"):
    item = db.get(WatchlistItem, symbol)
    eligible=data_valid and analysis_complete and minimum_history and liquidity_valid and score>=threshold
    if not eligible:
        if item:
            db.delete(item); db.commit()
        return None
    if analysis_mode=="ANALYSIS_ONLY":
        status="POSSIBLE_ENTRY_ANALYSIS_ONLY" if score>=entry_threshold and setup!="NONE" else "WATCHING_OFF_HOURS"
    else:
        status = "POSSIBLE_ENTRY" if score >= entry_threshold and setup != "NONE" else "WATCHING"
    if not item:
        item = WatchlistItem(symbol=symbol, score=score, setup=setup, status=status, reason=reason)
        db.add(item)
    else:
        item.score, item.setup, item.status, item.reason = score, setup, status, reason
        item.last_analyzed_at = datetime.now(timezone.utc)
    item.price,item.setup_quality,item.trend,item.structure=price,setup_quality,trend,structure
    item.support,item.resistance,item.rr,item.run_id=support,resistance,rr,run_id
    db.commit()
    return item
