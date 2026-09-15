from decimal import Decimal
from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    app_name: str = "BIST Sanal Portföy"
    api_prefix: str = "/api"
    database_url: str = "sqlite:///./bist_simulator.db"
    db_pool_size: int = 5
    db_max_overflow: int = 2
    db_pool_timeout_seconds: int = 15
    db_pool_recycle_seconds: int = 900
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
    scanner_symbol_limit: int = 30
    manual_scan_symbol_limit: int = 10
    auto_scan_enabled: bool = False
    operation_mode: str = "LIVE_PAPER"
    live_strategy_version: str = "V3_FROZEN_1"
    simple_entry_score: int = 60
    simple_max_open_positions: int = 1
    simple_symbol_limit: int = 28
    worker_poll_seconds: int = 20
    embedded_worker_enabled: bool = True
    market_open_poll_seconds: int = 300
    after_hours_poll_seconds: int = 900
    off_hours_scan_enabled: bool = True
    off_hours_scan_interval_minutes: int = 30
    off_hours_scan_symbol_limit: int = 30
    symbol_quarantine_failure_threshold: int = 3
    symbol_quarantine_minutes: int = 60
    ai_enabled: bool = True
    ai_provider: str = "groq"
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-20b"
    ai_min_score: int = 70
    ai_timeout_seconds: float = 25
    # Telegram alerts
    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    telegram_signal_alerts: bool = True
    telegram_off_hours_analysis: bool = False
    telegram_timeout_seconds: float = 15
    telegram_commands_enabled: bool = True
    telegram_command_poll_seconds: int = 15
    telegram_data_health_alerts: bool = True
    telegram_worker_alerts: bool = True
    telegram_startup_alert: bool = True
    news_enabled: bool = True
    kap_enabled: bool = True
    news_poll_minutes_open: int = 5
    news_poll_minutes_closed: int = 15
    news_telegram_min_importance: int = 80
    news_http_timeout_seconds: float = 12
    news_max_html_bytes: int = 2_000_000
    news_backfill_days: int = 90
    backfill_enabled: bool = True
    backfill_symbols_per_cycle: int = 2
    backfill_candle_limit: int = 1000
    market_memory_enabled: bool = True
    market_memory_reaction_batch: int = 20
    news_reconcile_batch_size: int = 25
    candle_retention_enabled: bool = False
    candle_retention_5m_days: int = 90
    candle_retention_15m_days: int = 730
    candle_retention_1h_days: int = 1825
    candle_retention_1d_days: int = 0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if isinstance(value, str) and value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @field_validator("market_data_provider", mode="before")
    @classmethod
    def normalize_market_data_provider(cls, value: str) -> str:
        """Normalize the legacy Render typo to the declared production policy."""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "yaho":
                return "hybrid"
            return normalized
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
