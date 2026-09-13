"""Add temporary scanner symbol health quarantine."""
from alembic import op
import sqlalchemy as sa

revision="0009_symbol_health"
down_revision="0008_off_hours_analysis"
branch_labels=None
depends_on=None


def upgrade():
    inspector=sa.inspect(op.get_bind())
    if "symbol_health" not in inspector.get_table_names():
        op.create_table(
            "symbol_health",
            sa.Column("symbol",sa.String(16),primary_key=True),
            sa.Column("consecutive_failures",sa.Integer(),nullable=False,server_default="0"),
            sa.Column("last_failure_at",sa.DateTime(timezone=True)),
            sa.Column("last_success_at",sa.DateTime(timezone=True)),
            sa.Column("quarantined_until",sa.DateTime(timezone=True)),
            sa.Column("last_error_type",sa.String(32)),
            sa.Column("last_error_message",sa.Text()),
        )
        op.create_index("ix_symbol_health_quarantined_until","symbol_health",["quarantined_until"])


def downgrade():
    pass
