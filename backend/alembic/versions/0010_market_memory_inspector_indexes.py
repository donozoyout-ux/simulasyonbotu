"""market memory inspector indexes

Revision ID: 0010_memory_inspector
Revises: 0009_symbol_health
"""

from alembic import op


revision = "0010_memory_inspector"
down_revision = "0009_symbol_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_analyses_symbol_signal_candle_time",
        "analyses",
        ["symbol", "signal_candle_time"],
    )
    op.create_index(
        "ix_market_snapshot_symbol_timestamp",
        "market_state_snapshots",
        ["symbol", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_snapshot_symbol_timestamp", table_name="market_state_snapshots")
    op.drop_index("ix_analyses_symbol_signal_candle_time", table_name="analyses")
