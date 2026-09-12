"""Backfill lifecycle timestamps on pre-market-memory news rows."""
from alembic import op

revision = "0005_archive_timestamps"
down_revision = "0004_market_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE news_items
        SET first_seen_at = COALESCE(first_seen_at, fetched_at, published_at),
            updated_at = COALESCE(updated_at, fetched_at, published_at)
        WHERE first_seen_at IS NULL OR updated_at IS NULL
    """)


def downgrade():
    # Historical lifecycle timestamps are data; never erase them on downgrade.
    pass
