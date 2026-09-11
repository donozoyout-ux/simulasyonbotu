"""Add non-destructive KAP/news persistence."""
from alembic import op
import sqlalchemy as sa

revision = "0003_news_engine"
down_revision = "0002_hardening"
branch_labels = None
depends_on = None


def upgrade():
    tables=set(sa.inspect(op.get_bind()).get_table_names())
    if "news_items" not in tables:
        op.create_table("news_items",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("symbol", sa.String(16), nullable=True),
        sa.Column("company_name", sa.String(160), nullable=True), sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False), sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False), sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("category", sa.String(48), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False), sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("ai_status", sa.String(32), nullable=True),
        sa.Column("ai_sentiment", sa.String(16), nullable=True), sa.Column("ai_importance", sa.Integer(), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True), sa.Column("ai_horizon", sa.String(24), nullable=True),
        sa.Column("ai_risks", sa.JSON(), nullable=False), sa.Column("ai_tags", sa.JSON(), nullable=False),
        sa.Column("ai_model", sa.String(80), nullable=True), sa.Column("telegram_sent", sa.Boolean(), nullable=False),
            sa.UniqueConstraint("source", "source_id", name="uq_news_source_id"), sa.UniqueConstraint("content_hash", name="uq_news_content_hash"))
        op.create_index("ix_news_items_symbol", "news_items", ["symbol"])
        op.create_index("ix_news_items_published_at", "news_items", ["published_at"])
    if "news_source_states" not in tables:
        op.create_table("news_source_states", sa.Column("source", sa.String(32), primary_key=True),
            sa.Column("status", sa.String(24), nullable=False), sa.Column("last_fetch_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_seen_id", sa.String(200), nullable=True), sa.Column("new_items", sa.Integer(), nullable=False),
            sa.Column("parse_errors", sa.Integer(), nullable=False), sa.Column("error", sa.String(500), nullable=True))


def downgrade():
    tables=set(sa.inspect(op.get_bind()).get_table_names())
    if "news_source_states" in tables:op.drop_table("news_source_states")
    if "news_items" in tables:op.drop_table("news_items")
