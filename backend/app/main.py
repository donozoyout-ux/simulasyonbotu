from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.db.session import Base, SessionLocal, engine
from app.db.schema_compat import ensure_schema_compatibility
from app.models import *  # noqa: F401,F403
from app.portfolio.portfolio_manager import ensure_portfolio, take_snapshot
from app.services.forward_test import ensure_forward_run

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(settings.log_level)
    Base.metadata.create_all(engine)
    ensure_schema_compatibility(engine)
    with SessionLocal() as db:
        ensure_portfolio(db, settings.initial_balance)
        if settings.operation_mode=="LIVE_PAPER":run=ensure_forward_run(db,settings)
        else:run=None
        if not db.query(PortfolioSnapshot).first(): take_snapshot(db, settings.initial_balance,run.run_id if run else None)
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router, prefix=settings.api_prefix)
