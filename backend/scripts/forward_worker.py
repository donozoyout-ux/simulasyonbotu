import time

from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.db.schema_compat import ensure_schema_compatibility
from app.db.session import Base,SessionLocal,engine
from app.services.forward_worker import ForwardWorker


def main():
    config=get_settings();configure_logging(config.log_level);Base.metadata.create_all(engine);ensure_schema_compatibility(engine)
    while True:
        with SessionLocal() as db:
            ForwardWorker(db,config).run_once()
        time.sleep(max(5,config.worker_poll_seconds))


if __name__=="__main__":main()
