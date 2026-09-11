from decimal import Decimal
from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    app_name: str = "BIST Sanal Portföy"
    api_prefix: str = "/api"
    database_url: str = "sqlite:///./bist_simulator.db"
    data_mode: str = "live"

    # Market data
    market_data_provider: str = "hybrid"
    eodhd_api_token: str | None = None
    twelve_data_api_key: str | None = None
    hybrid_price_tolerance_pct: Decimal = Decimal("0.015")
    allow_mock_fallback: bool = False

    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"
    frontend_url: str = "http://localhost:3000"

    initial_balance: Decimal = Decimal("5000.00")
    commission_rate: Decimal = Decimal("0.0010")
    slippage_rate: Decimal = Decimal("0.0005")
    risk_per_trade_pct: Decimal = Decimal("0.005")
    max_position_size_pct: Decimal = Decimal("0.20")
    minimum_cash_reserve_pct: Decimal = Decimal("0.10")
    max_open_positions: int = 4
    min_rr: Decimal = Decimal("1.50")
    watchlist_score: int = 70
    entry_score: int = 82
    stale_after_minutes: int = 90
    bist_timezone: str = "Europe/Istanbul"
    bist_session_open: str = "10:00"
    bist_session_close: str = "18:00"
    minimum_stop_atr_pct: Decimal = Decimal("0.35")
    maximum_stop_atr_pct: Decimal = Decimal("4.00")
    intrabar_policy: str = "conservative"
    strategy_version: str = "v3"
    scan_interval_minutes: int = 15
    auto_scan_enabled: bool = False
    operation_mode: str = "LIVE_PAPER"
    live_strategy_version: str = "V3_FROZEN_1"
    worker_poll_seconds: int = 20
    ai_enabled: bool = True
    ai_provider: str = "groq"
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-20b"
    ai_min_score: int = 70
    ai_timeout_seconds: float = 25
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if isinstance(value, str) and value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
