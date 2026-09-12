"""Add non-destructive market memory and archive metadata."""
from alembic import op
import sqlalchemy as sa

revision = "0004_market_memory"
down_revision = "0003_news_engine"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "news_items" in tables:
        columns = {column["name"] for column in inspector.get_columns("news_items")}
        additions = {
            "first_seen_at": sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
            "updated_at": sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            "telegram_sent_at": sa.Column("telegram_sent_at", sa.DateTime(timezone=True), nullable=True),
            "overnight_news": sa.Column("overnight_news", sa.Boolean(), nullable=False, server_default=sa.false()),
        }
        for name, column in additions.items():
            if name not in columns:
                op.add_column("news_items", column)
        indexes = {item["name"] for item in sa.inspect(bind).get_indexes("news_items")}
        if "ix_news_items_symbol_published_at" not in indexes:
            op.create_index("ix_news_items_symbol_published_at", "news_items", ["symbol", "published_at"])
        if "ix_news_items_overnight_news" not in indexes:
            op.create_index("ix_news_items_overnight_news", "news_items", ["overnight_news"])

    if "market_state_snapshots" not in tables:
        op.create_table("market_state_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("symbol", sa.String(16), nullable=False),
            sa.Column("timeframe", sa.String(8), nullable=False), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(16), nullable=False), sa.Column("price", sa.Numeric(18,4), nullable=False),
            sa.Column("ema20", sa.Numeric(18,4)), sa.Column("ema50", sa.Numeric(18,4)), sa.Column("ema200", sa.Numeric(18,4)),
            sa.Column("rsi", sa.Numeric(12,6)), sa.Column("macd", sa.Numeric(18,8)), sa.Column("macd_signal", sa.Numeric(18,8)),
            sa.Column("macd_histogram", sa.Numeric(18,8)), sa.Column("bb_upper", sa.Numeric(18,4)),
            sa.Column("bb_middle", sa.Numeric(18,4)), sa.Column("bb_lower", sa.Numeric(18,4)), sa.Column("atr", sa.Numeric(18,4)),
            sa.Column("atr_pct", sa.Numeric(12,6)), sa.Column("vwap", sa.Numeric(18,4)), sa.Column("rvol", sa.Numeric(12,6)),
            sa.Column("volume_sma20", sa.Numeric(24,2)), sa.Column("trend", sa.String(24)), sa.Column("market_structure", sa.String(32)),
            sa.Column("swing_high", sa.Numeric(18,4)), sa.Column("swing_low", sa.Numeric(18,4)), sa.Column("support", sa.Numeric(18,4)),
            sa.Column("resistance", sa.Numeric(18,4)), sa.Column("bos", sa.String(24)), sa.Column("choch", sa.String(24)),
            sa.Column("setup", sa.String(32)), sa.Column("setup_quality", sa.Integer()), sa.Column("technical_score", sa.Integer()),
            sa.Column("risk_reward", sa.Numeric(12,6)),
            sa.Column("relative_strength_1d", sa.Numeric(12,6)), sa.Column("relative_strength_5d", sa.Numeric(12,6)),
            sa.Column("relative_strength_20d", sa.Numeric(12,6)), sa.Column("xu100_price", sa.Numeric(18,4)),
            sa.Column("data_source", sa.String(32), nullable=False), sa.Column("data_quality", sa.String(16), nullable=False),
            sa.UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_market_snapshot_identity"))
        op.create_index("ix_market_snapshot_symbol_timeframe_timestamp", "market_state_snapshots", ["symbol", "timeframe", "timestamp"])

    if "news_market_reactions" not in tables:
        op.create_table("news_market_reactions",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("news_id", sa.Integer(), sa.ForeignKey("news_items.id"), nullable=False),
            sa.Column("symbol", sa.String(16), nullable=False), sa.Column("price_before", sa.Numeric(18,4)),
            sa.Column("previous_close", sa.Numeric(18,4)), sa.Column("next_open", sa.Numeric(18,4)), sa.Column("next_close", sa.Numeric(18,4)),
            sa.Column("gap_pct", sa.Numeric(12,6)), sa.Column("return_15m", sa.Numeric(12,6)), sa.Column("return_1h", sa.Numeric(12,6)),
            sa.Column("return_1d", sa.Numeric(12,6)), sa.Column("return_5d", sa.Numeric(12,6)), sa.Column("xu100_return_1d", sa.Numeric(12,6)),
            sa.Column("abnormal_return_1d", sa.Numeric(12,6)), sa.Column("volume_change", sa.Numeric(12,6)),
            sa.Column("rvol_after", sa.Numeric(12,6)), sa.Column("evaluated_at", sa.DateTime(timezone=True)),
            sa.Column("status", sa.String(24), nullable=False), sa.UniqueConstraint("news_id", "symbol", name="uq_news_reaction_identity"))
        op.create_index("ix_news_reaction_news_symbol", "news_market_reactions", ["news_id", "symbol"])

    if "backfill_states" not in tables:
        op.create_table("backfill_states", sa.Column("task", sa.String(48), primary_key=True),
            sa.Column("status", sa.String(24), nullable=False), sa.Column("cursor", sa.Integer(), nullable=False),
            sa.Column("processed_items", sa.Integer(), nullable=False), sa.Column("last_run_at", sa.DateTime(timezone=True)),
            sa.Column("error", sa.String(500)), sa.Column("details", sa.JSON(), nullable=False))


def downgrade():
    # Intentionally non-destructive: production market memory is retained.
    pass
