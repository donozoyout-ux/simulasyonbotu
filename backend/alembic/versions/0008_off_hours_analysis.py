"""Add non-trading off-hours analysis metadata."""
from alembic import op
import sqlalchemy as sa

revision="0008_off_hours_analysis"
down_revision="0007_news_reconciliation"
branch_labels=None
depends_on=None

def _columns(table):
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}

def upgrade():
    scan_columns=_columns("scan_runs")
    with op.batch_alter_table("scan_runs") as batch:
        if "analysis_mode" not in scan_columns:batch.add_column(sa.Column("analysis_mode",sa.String(16),nullable=False,server_default="LIVE"))
        if "market_open" not in scan_columns:batch.add_column(sa.Column("market_open",sa.Boolean(),nullable=False,server_default=sa.true()))
        if "source_candle_timestamp" not in scan_columns:batch.add_column(sa.Column("source_candle_timestamp",sa.DateTime(timezone=True)))
    indexes={item["name"] for item in sa.inspect(op.get_bind()).get_indexes("scan_runs")}
    if "ix_scan_runs_analysis_mode" not in indexes:op.create_index("ix_scan_runs_analysis_mode","scan_runs",["analysis_mode"])
    snapshot_columns=_columns("market_state_snapshots")
    if "analysis_mode" not in snapshot_columns:
        op.add_column("market_state_snapshots",sa.Column("analysis_mode",sa.String(16),nullable=False,server_default="LIVE"))

def downgrade():
    pass
