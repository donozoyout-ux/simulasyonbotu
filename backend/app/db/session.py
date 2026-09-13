from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine_options = {"pool_pre_ping": True}
if not settings.database_url.startswith("sqlite"):
    engine_options.update(pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds, pool_recycle=settings.db_pool_recycle_seconds)
engine = create_engine(settings.database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

