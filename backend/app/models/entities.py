from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
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
    analysis_mode: Mapped[str] = mapped_column(String(16), default="LIVE", index=True)
    market_open: Mapped[bool] = mapped_column(Boolean, default=True)
    source_candle_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_news_source_id"),
        UniqueConstraint("content_hash", name="uq_news_content_hash"),
        Index("ix_news_items_symbol_published_at", "symbol", "published_at"),
        Index("ix_news_items_source_published_at", "source", "published_at"),
        Index("ix_news_items_ai_status", "ai_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    company_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1000))
    category: Mapped[str] = mapped_column(String(48), default="OTHER")
    status: Mapped[str] = mapped_column(String(32), default="OK")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    ai_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    ai_importance: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_horizon: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ai_risks: Mapped[list] = mapped_column(JSON, default=list)
    ai_tags: Mapped[list] = mapped_column(JSON, default=list)
    ai_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    overnight_news: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    telegram_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    ingestion_mode: Mapped[str] = mapped_column(String(16), default="LIVE")
    symbol_match_method: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    symbol_match_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unmatched_reason: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    detected_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    best_candidate: Mapped[str | None] = mapped_column(String(16), nullable=True)
    best_candidate_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class NewsCompanyLink(Base):
    __tablename__ = "news_company_links"
    __table_args__ = (
        UniqueConstraint("news_id", "symbol", name="uq_news_company_link_identity"),
        Index("ix_news_company_link_news_primary", "news_id", "is_primary"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news_items.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[int] = mapped_column(Integer)
    match_method: Mapped[str] = mapped_column(String(32))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class NewsSourceState(Base):
    __tablename__ = "news_source_states"
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="NO_DATA")
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    new_items: Mapped[int] = mapped_column(Integer, default=0)
    parse_errors: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_type: Mapped[str] = mapped_column(String(24), default="RSS")
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    poll_interval: Mapped[int] = mapped_column(Integer, default=900)
    supports_backfill: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_symbol_mapping: Mapped[bool] = mapped_column(Boolean, default=True)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_item_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    items_fetched: Mapped[int] = mapped_column(Integer, default=0)
    items_inserted: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)


class MarketStateSnapshot(Base):
    __tablename__ = "market_state_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_market_snapshot_identity"),
        Index("ix_market_snapshot_symbol_timeframe_timestamp", "symbol", "timeframe", "timestamp"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), default="15m")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="RECORDED")
    price: Mapped[Decimal] = mapped_column(money)
    ema20: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    ema50: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    ema200: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    rsi: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    macd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    macd_signal: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    macd_histogram: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    bb_upper: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    bb_middle: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    bb_lower: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    atr: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    atr_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    vwap: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    rvol: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    volume_sma20: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)
    trend: Mapped[str | None] = mapped_column(String(24), nullable=True)
    market_structure: Mapped[str | None] = mapped_column(String(32), nullable=True)
    swing_high: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    swing_low: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    support: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    resistance: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    bos: Mapped[str | None] = mapped_column(String(24), nullable=True)
    choch: Mapped[str | None] = mapped_column(String(24), nullable=True)
    setup: Mapped[str | None] = mapped_column(String(32), nullable=True)
    setup_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    technical_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_reward: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    relative_strength_1d: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    relative_strength_5d: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    relative_strength_20d: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    xu100_price: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    data_source: Mapped[str] = mapped_column(String(32))
    data_quality: Mapped[str] = mapped_column(String(16), default="VALID")
    analysis_mode: Mapped[str] = mapped_column(String(16), default="LIVE")


class NewsMarketReaction(Base):
    __tablename__ = "news_market_reactions"
    __table_args__ = (
        UniqueConstraint("news_id", "symbol", name="uq_news_reaction_identity"),
        Index("ix_news_reaction_news_symbol", "news_id", "symbol"),
        Index("ix_news_reaction_status_due", "status", "next_evaluation_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news_items.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    price_before: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    previous_close: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    next_open: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    next_close: Mapped[Decimal | None] = mapped_column(money, nullable=True)
    gap_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    return_15m: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    return_1h: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    return_1d: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    return_5d: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    xu100_return_1d: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    abnormal_return_1d: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    volume_change: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    rvol_after: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    next_evaluation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_evaluation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pre_return_15m: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    first_15m_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    first_1h_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    eod_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)


class BackfillState(Base):
    __tablename__ = "backfill_states"
    task: Mapped[str] = mapped_column(String(48), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    processed_items: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class DataCollectionActivity(Base):
    __tablename__ = "data_collection_activity"
    __table_args__ = (Index("ix_collection_activity_module_created", "module", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    module: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
