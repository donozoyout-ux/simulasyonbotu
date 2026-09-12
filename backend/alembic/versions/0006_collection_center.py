"""Add non-destructive collector health and reaction queue state."""
from alembic import op
import sqlalchemy as sa

revision = "0006_collection_center"
down_revision = "0005_archive_timestamps"
branch_labels = None
depends_on = None


def upgrade():
    news_columns = {
        "telegram_eligible": sa.Column("telegram_eligible", sa.Boolean(), nullable=False, server_default=sa.true()),
        "ingestion_mode": sa.Column("ingestion_mode", sa.String(16), nullable=False, server_default="LIVE"),
    }
    source_columns = {
        "source_type": sa.Column("source_type", sa.String(24), nullable=False, server_default="RSS"),
        "base_url": sa.Column("base_url", sa.String(500)), "enabled": sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        "priority": sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        "poll_interval": sa.Column("poll_interval", sa.Integer(), nullable=False, server_default="900"),
        "supports_backfill": sa.Column("supports_backfill", sa.Boolean(), nullable=False, server_default=sa.false()),
        "supports_symbol_mapping": sa.Column("supports_symbol_mapping", sa.Boolean(), nullable=False, server_default=sa.true()),
        "next_poll_at": sa.Column("next_poll_at", sa.DateTime(timezone=True)), "last_poll_at": sa.Column("last_poll_at", sa.DateTime(timezone=True)),
        "last_success_at": sa.Column("last_success_at", sa.DateTime(timezone=True)), "last_item_at": sa.Column("last_item_at", sa.DateTime(timezone=True)),
        "items_fetched": sa.Column("items_fetched", sa.Integer(), nullable=False, server_default="0"),
        "items_inserted": sa.Column("items_inserted", sa.Integer(), nullable=False, server_default="0"),
        "duplicates": sa.Column("duplicates", sa.Integer(), nullable=False, server_default="0"),
        "errors": sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        "consecutive_failures": sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
    }
    reaction_columns = {
        "next_evaluation_at": sa.Column("next_evaluation_at", sa.DateTime(timezone=True)),
        "last_evaluation_at": sa.Column("last_evaluation_at", sa.DateTime(timezone=True)),
        "attempts": sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        "error": sa.Column("error", sa.String(500)), "pre_return_15m": sa.Column("pre_return_15m", sa.Numeric(12,6)),
        "first_15m_return": sa.Column("first_15m_return", sa.Numeric(12,6)), "first_1h_return": sa.Column("first_1h_return", sa.Numeric(12,6)),
        "eod_return": sa.Column("eod_return", sa.Numeric(12,6)),
    }
    inspector = sa.inspect(op.get_bind())
    for table, additions in (("news_items", news_columns), ("news_source_states", source_columns), ("news_market_reactions", reaction_columns)):
        existing = {item["name"] for item in inspector.get_columns(table)}
        for name, column in additions.items():
            if name not in existing: op.add_column(table, column)
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("news_items")}
    if "ix_news_items_source_published_at" not in indexes: op.create_index("ix_news_items_source_published_at", "news_items", ["source", "published_at"])
    if "ix_news_items_symbol_published_at" not in indexes: op.create_index("ix_news_items_symbol_published_at", "news_items", ["symbol", "published_at"])
    if "ix_news_items_ai_status" not in indexes: op.create_index("ix_news_items_ai_status", "news_items", ["ai_status"])
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("news_source_states")}
    if "ix_news_source_states_next_poll_at" not in indexes: op.create_index("ix_news_source_states_next_poll_at", "news_source_states", ["next_poll_at"])
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("news_market_reactions")}
    if "ix_news_reaction_status_due" not in indexes: op.create_index("ix_news_reaction_status_due", "news_market_reactions", ["status", "next_evaluation_at"])
    if "data_collection_activity" not in inspector.get_table_names():
        op.create_table("data_collection_activity", sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("module", sa.String(32), nullable=False),
            sa.Column("subject", sa.String(64)), sa.Column("action", sa.String(32), nullable=False),
            sa.Column("status", sa.String(24), nullable=False), sa.Column("detail", sa.String(500)))
    activity_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("data_collection_activity")}
    if "ix_collection_activity_created_at" not in activity_indexes: op.create_index("ix_collection_activity_created_at", "data_collection_activity", ["created_at"])
    if "ix_collection_activity_module" not in activity_indexes: op.create_index("ix_collection_activity_module", "data_collection_activity", ["module"])
    if "ix_collection_activity_module_created" not in activity_indexes: op.create_index("ix_collection_activity_module_created", "data_collection_activity", ["module", "created_at"])


def downgrade():
    pass
