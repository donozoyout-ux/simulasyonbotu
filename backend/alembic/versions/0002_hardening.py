"""V2 audit and hardening columns."""
from alembic import op
import sqlalchemy as sa

revision="0002_hardening"
down_revision="0001_initial"
branch_labels=None
depends_on=None


def _columns(inspector,table): return {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    bind=op.get_bind(); inspector=sa.inspect(bind); tables=set(inspector.get_table_names())
    if "analyses" in tables:
        cols=_columns(inspector,"analyses")
        with op.batch_alter_table("analyses") as batch:
            if "signal_candle_time" not in cols: batch.add_column(sa.Column("signal_candle_time",sa.DateTime(timezone=True),nullable=True))
            if "data_valid" not in cols: batch.add_column(sa.Column("data_valid",sa.Boolean(),nullable=False,server_default=sa.true()))
    if "orders" in tables:
        cols=_columns(inspector,"orders")
        with op.batch_alter_table("orders") as batch:
            if "commission" not in cols: batch.add_column(sa.Column("commission",sa.Numeric(18,4),nullable=False,server_default="0"))
            if "slippage_cost" not in cols: batch.add_column(sa.Column("slippage_cost",sa.Numeric(18,4),nullable=False,server_default="0"))
            if "idempotency_key" not in cols: batch.add_column(sa.Column("idempotency_key",sa.String(160),nullable=True))
            if "signal_candle_time" not in cols: batch.add_column(sa.Column("signal_candle_time",sa.DateTime(timezone=True),nullable=True))
        indexes={i["name"] for i in sa.inspect(bind).get_indexes("orders")}
        if "uq_orders_idempotency_key" not in indexes: op.create_index("uq_orders_idempotency_key","orders",["idempotency_key"],unique=True)
    if "positions" in tables:
        cols=_columns(inspector,"positions")
        with op.batch_alter_table("positions") as batch:
            if "entry_slippage_cost" not in cols: batch.add_column(sa.Column("entry_slippage_cost",sa.Numeric(18,4),nullable=False,server_default="0"))
    if "trades" in tables:
        cols=_columns(inspector,"trades")
        with op.batch_alter_table("trades") as batch:
            if "commission" not in cols: batch.add_column(sa.Column("commission",sa.Numeric(18,4),nullable=False,server_default="0"))
            if "slippage_cost" not in cols: batch.add_column(sa.Column("slippage_cost",sa.Numeric(18,4),nullable=False,server_default="0"))
    if "scan_runs" not in tables:
        op.create_table("scan_runs",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("started_at",sa.DateTime(timezone=True),nullable=False),
            sa.Column("completed_at",sa.DateTime(timezone=True)),sa.Column("duration_ms",sa.Integer()),sa.Column("provider",sa.String(32),nullable=False),
            sa.Column("data_mode",sa.String(16),nullable=False),sa.Column("total_symbols",sa.Integer(),nullable=False),sa.Column("valid_symbols",sa.Integer(),nullable=False),
            sa.Column("failed_symbols",sa.Integer(),nullable=False),sa.Column("stale_symbols",sa.Integer(),nullable=False),sa.Column("funnel",sa.JSON(),nullable=False),sa.Column("errors",sa.JSON(),nullable=False))


def downgrade():
    op.drop_table("scan_runs")
