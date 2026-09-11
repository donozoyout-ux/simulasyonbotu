from decimal import Decimal
from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    scan_interval_minutes: int | None = Field(None, ge=1, le=1440)
    watchlist_score: int | None = Field(None, ge=0, le=100)
    entry_score: int | None = Field(None, ge=0, le=100)
    risk_per_trade_pct: Decimal | None = Field(None, gt=0, le=Decimal("0.05"))
    max_position_size_pct: Decimal | None = Field(None, gt=0, le=Decimal("1"))
    max_open_positions: int | None = Field(None, ge=1, le=20)
    min_rr: Decimal | None = Field(None, ge=Decimal("1"), le=Decimal("10"))
    commission_rate: Decimal | None = Field(None, ge=0, le=Decimal("0.02"))
    slippage_rate: Decimal | None = Field(None, ge=0, le=Decimal("0.02"))


class PortfolioReset(BaseModel):
    confirmation: str


class PaperTradingControl(BaseModel):
    paused: bool
