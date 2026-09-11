from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


money = Numeric(18, 4)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Symbol(Base):
    __tablename__ = "symbols"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "timestamp"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[Decimal] = mapped_column(money)
    high: Mapped[Decimal] = mapped_column(money)
    low: Mapped[Decimal] = mapped_column(money)
    close: Mapped[Decimal] = mapped_column(money)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 2))
    source: Mapped[str] = mapped_column(String(32))


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    price: Mapped[Decimal] = mapped_column(money)
    score: Mapped[int] = mapped_column(Integer)
    trend: Mapped[str] = mapped_column(String(24))
    market_structure: Mapped[str] = mapped_column(String(24))
    setup: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON)
    data_source: Mapped[str] = mapped_column(String(32))
    signal_candle_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    data_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    strategy_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ai_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    score: Mapped[int] = mapped_column(Integer)
    setup: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    price: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    setup_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trend: Mapped[str | None] = mapped_column(String(24), nullable=True)
    structure: Mapped[str | None] = mapped_column(String(24), nullable=True)
    support: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    resistance: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    rr: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class Portfolio(Base):
    __tablename__ = "portfolio"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    initial_balance: Mapped[Decimal] = mapped_column(money)
    cash_balance: Mapped[Decimal] = mapped_column(money)
    realized_pnl: Mapped[Decimal] = mapped_column(money, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8), default="LONG")
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[Decimal] = mapped_column(money)
    current_price: Mapped[Decimal] = mapped_column(money)
    stop_price: Mapped[Decimal] = mapped_column(money)
    target_price: Mapped[Decimal] = mapped_column(money)
    entry_fees: Mapped[Decimal] = mapped_column(money)
    entry_slippage_cost: Mapped[Decimal] = mapped_column(money, default=Decimal("0"))
    setup_type: Mapped[str] = mapped_column(String(32))
    signal_score: Mapped[int] = mapped_column(Integer)
    entry_reason: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer)
    requested_price: Mapped[Decimal] = mapped_column(money)
    fill_price: Mapped[Decimal] = mapped_column(money)
    fees: Mapped[Decimal] = mapped_column(money)
    commission: Mapped[Decimal] = mapped_column(money, default=Decimal("0"))
    slippage_cost: Mapped[Decimal] = mapped_column(money, default=Decimal("0"))
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True, index=True)
    signal_candle_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    strategy_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_amount: Mapped[Decimal | None] = mapped_column(money, nullable=True)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8), default="LONG")
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[Decimal] = mapped_column(money)
    exit_price: Mapped[Decimal] = mapped_column(money)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    position_value: Mapped[Decimal] = mapped_column(money)
    stop_price: Mapped[Decimal] = mapped_column(money)
    target_price: Mapped[Decimal] = mapped_column(money)
    fees: Mapped[Decimal] = mapped_column(money)
    commission: Mapped[Decimal] = mapped_column(money, default=Decimal("0"))
    slippage_cost: Mapped[Decimal] = mapped_column(money, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(money)
    return_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    status: Mapped[str] = mapped_column(String(16), default="CLOSED")
    setup_type: Mapped[str] = mapped_column(String(32))
    signal_score: Mapped[int] = mapped_column(Integer)
    entry_reason: Mapped[str] = mapped_column(Text)
    exit_reason: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)


class DecisionLog(Base):
    __tablename__ = "decision_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(24))
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    strategy_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    cash: Mapped[Decimal] = mapped_column(money)
    invested_value: Mapped[Decimal] = mapped_column(money)
    portfolio_value: Mapped[Decimal] = mapped_column(money)
    realized_pnl: Mapped[Decimal] = mapped_column(money)
    unrealized_pnl: Mapped[Decimal] = mapped_column(money)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    data_mode: Mapped[str] = mapped_column(String(16))
    total_symbols: Mapped[int] = mapped_column(Integer, default=0)
    valid_symbols: Mapped[int] = mapped_column(Integer, default=0)
    failed_symbols: Mapped[int] = mapped_column(Integer, default=0)
    stale_symbols: Mapped[int] = mapped_column(Integer, default=0)
    funnel: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), default="15m")
    closed_candle_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watchlist_count: Mapped[int] = mapped_column(Integer, default=0)
    signals: Mapped[int] = mapped_column(Integer, default=0)
    entries: Mapped[int] = mapped_column(Integer, default=0)


class ForwardRun(Base):
    __tablename__ = "forward_runs"
    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="RUNNING", index=True)
    strategy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    strategy_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    initial_balance: Mapped[Decimal] = mapped_column(money, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    benchmark_start_price: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    benchmark_latest_price: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    benchmark_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessedCandle(Base):
    __tablename__ = "processed_candles"
    __table_args__ = (UniqueConstraint("run_id", "timeframe", "closed_candle_timestamp"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), default="15m")
    closed_candle_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    scan_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="STARTED")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    __table_args__ = (UniqueConstraint("run_id", "date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    starting_equity: Mapped[Decimal] = mapped_column(money)
    ending_equity: Mapped[Decimal] = mapped_column(money)
    daily_pnl: Mapped[Decimal] = mapped_column(money)
    daily_return_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    fees: Mapped[Decimal] = mapped_column(money, default=Decimal("0"))
    largest_position: Mapped[Decimal] = mapped_column(money, default=Decimal("0"))
    cash_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("100"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
