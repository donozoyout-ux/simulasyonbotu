import time
from datetime import datetime, timezone

from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.db.schema_compat import ensure_schema_compatibility
from app.db.session import Base, SessionLocal, engine
from app.market_data.market_session import BistMarketSession
from app.services.forward_worker import ForwardWorker
from app.news.service import NewsService


def next_sleep(config) -> int:
    session = BistMarketSession.from_config(config)
    if session.is_open(datetime.now(timezone.utc)):
        return max(60, min(int(config.market_open_poll_seconds),int(config.news_poll_minutes_open)*60))
    return max(60, min(int(config.after_hours_poll_seconds),int(config.news_poll_minutes_closed)*60))


def main():
    config = get_settings()
    configure_logging(config.log_level)
    Base.metadata.create_all(engine)
    ensure_schema_compatibility(engine)
    while True:
        with SessionLocal() as db:
            ForwardWorker(db, config).run_once()
            if config.news_enabled:
                try: NewsService(db,config).refresh()
                except Exception: db.rollback()
        time.sleep(next_sleep(config))


if __name__ == "__main__":
    main()
