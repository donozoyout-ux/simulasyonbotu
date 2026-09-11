from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config.settings import get_settings
from app.db.session import Base, get_db
from app.api.routes import router
from app.models import Candle, Portfolio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from app.portfolio.portfolio_manager import ensure_portfolio

config = get_settings()
config.data_mode = "mock"
config.market_data_provider = "mock"
config.auto_scan_enabled = False

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine)


def override_db():
    with Session(engine, expire_on_commit=False) as session:
        ensure_portfolio(session, config.initial_balance)
        yield session


app = FastAPI()
app.include_router(router, prefix=config.api_prefix)
app.dependency_overrides[get_db] = override_db


def test_health_portfolio_and_mock_scan():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["real_orders"] is False
        portfolio = client.get("/api/portfolio").json()
        assert portfolio["initial_balance"] == 5000
        scan = client.post("/api/scanner/run?max_symbols=2")
        assert scan.status_code == 200
        assert scan.json()["analyzed"] == 2
        results = client.get("/api/scanner/results").json()
        assert len(results) >= 2
        assert all(row["data_source"] == "mock" for row in results[:2])
        research = client.get("/api/strategy-health")
        assert research.status_code == 200
        assert research.json()["status"] in {"READY", "NO_RESEARCH_REPORT"}
        if research.json()["status"] == "READY" and "v4" in research.json():
            assert research.json()["v4"]["strategy_config_hash"]
            assert research.json()["v4"]["evaluation_rows"] >= 0
        qualification = client.get("/api/provider-qualification")
        assert qualification.status_code == 200
        assert qualification.json()["status"] in {"READY", "PARTIAL", "WAITING_FOR_PROVIDER_CREDENTIAL", "BLOCKED", "NO_PROVIDER_QUALIFICATION_REPORT"}


def test_reset_requires_exact_confirmation():
    with TestClient(app) as client:
        assert client.post("/api/portfolio/reset", json={"confirmation": "yes"}).status_code == 400
        ok = client.post("/api/portfolio/reset", json={"confirmation": "RESET PAPER PORTFOLIO"})
        assert ok.status_code == 200
        assert ok.json()["initial_balance"] == 5000


def test_news_endpoints_are_available_without_exposing_secrets():
    with TestClient(app) as client:
        assert client.get("/api/news").status_code==200
        health=client.get("/api/news/health")
        assert health.status_code==200 and "sources" in health.json()
        payload=str(client.get("/api/health").json())
        assert "GROQ_API_KEY" not in payload and "DATABASE_URL" not in payload and "TELEGRAM_BOT_TOKEN" not in payload


def test_5m_candle_endpoint_returns_backend_indicators():
    with Session(engine) as db:
        db.query(Candle).filter(Candle.symbol=="FIVE",Candle.timeframe=="5m").delete()
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        for index in range(220):
            price=Decimal(100)+Decimal(index)/10
            db.add(Candle(symbol="FIVE",timeframe="5m",timestamp=start+timedelta(minutes=5*index),open=price,high=price+1,low=price-1,close=price,volume=Decimal(1000+index),source="test"))
        db.commit()
    with TestClient(app) as client:
        response=client.get("/api/candles/FIVE?timeframe=5m&limit=220")
        assert response.status_code==200 and len(response.json())==220
        assert response.json()[-1]["indicators"]["ema200"] is not None
