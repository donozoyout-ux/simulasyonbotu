from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.db.session import Base, SessionLocal, engine
from app.db.schema_compat import ensure_schema_compatibility
from app.models import *  # noqa: F401,F403
from app.portfolio.portfolio_manager import ensure_portfolio, take_snapshot
from app.services.forward_test import ensure_forward_run
from app.services.embedded_worker import EmbeddedWorker
from app.services.telegram_commands import TelegramCommandPoller

settings = get_settings()
embedded_worker = EmbeddedWorker(settings)
telegram_command_poller = TelegramCommandPoller(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(settings.log_level)
    Base.metadata.create_all(engine)
    ensure_schema_compatibility(engine)
    with SessionLocal() as db:
        ensure_portfolio(db, settings.initial_balance)
        if settings.operation_mode == "LIVE_PAPER":
            run = ensure_forward_run(db, settings)
        else:
            run = None
        if not db.query(PortfolioSnapshot).first():
            take_snapshot(db, settings.initial_balance, run.run_id if run else None)
    if settings.embedded_worker_enabled and settings.operation_mode == "LIVE_PAPER":
        embedded_worker.start()
    telegram_command_poller.start()
    try:
        yield
    finally:
        if settings.embedded_worker_enabled:
            embedded_worker.stop()
        telegram_command_poller.stop()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes are registered first so /api/* and /docs keep working.
app.include_router(router, prefix=settings.api_prefix)

frontend_dir = Path(__file__).resolve().parents[2] / "frontend_out"
local_frontend_dir = Path(__file__).resolve().parents[2] / "frontend" / "out"
if not frontend_dir.exists() and local_frontend_dir.exists():
    frontend_dir = local_frontend_dir

if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
else:
    @app.get("/", include_in_schema=False)
    def root():
        return JSONResponse(
            {
                "status": "backend_ready",
                "message": "Frontend build not found. In production the root Dockerfile builds and bundles it.",
                "docs": "/docs",
            }
        )
