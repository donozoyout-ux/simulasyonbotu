"""Add deterministic news/company reconciliation state."""
from alembic import op
import sqlalchemy as sa

revision="0007_news_reconciliation"
down_revision="0006_collection_center"
branch_labels=None
depends_on=None

def upgrade():
    inspector=sa.inspect(op.get_bind())
    existing={item["name"] for item in inspector.get_columns("news_items")}
    additions={
        "symbol_match_method":sa.Column("symbol_match_method",sa.String(32)),
        "symbol_match_confidence":sa.Column("symbol_match_confidence",sa.Integer()),
        "unmatched_reason":sa.Column("unmatched_reason",sa.String(48)),
        "detected_company":sa.Column("detected_company",sa.String(200)),
        "best_candidate":sa.Column("best_candidate",sa.String(16)),
        "best_candidate_confidence":sa.Column("best_candidate_confidence",sa.Integer()),
        "source_metadata":sa.Column("source_metadata",sa.JSON(),nullable=False,server_default=sa.text("'{}'")),
    }
    for name,column in additions.items():
        if name not in existing:op.add_column("news_items",column)
    indexes={item["name"] for item in sa.inspect(op.get_bind()).get_indexes("news_items")}
    if "ix_news_items_symbol_match_method" not in indexes:op.create_index("ix_news_items_symbol_match_method","news_items",["symbol_match_method"])
    if "ix_news_items_unmatched_reason" not in indexes:op.create_index("ix_news_items_unmatched_reason","news_items",["unmatched_reason"])
    if "news_company_links" not in inspector.get_table_names():
        op.create_table("news_company_links",sa.Column("id",sa.Integer(),primary_key=True),
            sa.Column("news_id",sa.Integer(),sa.ForeignKey("news_items.id"),nullable=False),
            sa.Column("symbol",sa.String(16),nullable=False),sa.Column("confidence",sa.Integer(),nullable=False),
            sa.Column("match_method",sa.String(32),nullable=False),sa.Column("is_primary",sa.Boolean(),nullable=False,server_default=sa.false()),
            sa.UniqueConstraint("news_id","symbol",name="uq_news_company_link_identity"))
        op.create_index("ix_news_company_links_news_id","news_company_links",["news_id"])
        op.create_index("ix_news_company_links_symbol","news_company_links",["symbol"])
        op.create_index("ix_news_company_link_news_primary","news_company_links",["news_id","is_primary"])

def downgrade():
    pass
