"""Create normalized instruments and durable discovery baselines."""

import sqlalchemy as sa

from alembic import op

revision = "0001_instruments"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_symbol", sa.String(255), nullable=False),
        sa.Column("ticker", sa.String(255), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("category", sa.String(255)),
        sa.Column("subcategory", sa.String(255)),
        sa.Column("currency", sa.String(32)),
        sa.Column("country", sa.String(100)),
        sa.Column("exchange", sa.String(100)),
        sa.Column("asset_type", sa.String(100)),
        sa.Column("source_url", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "source_symbol", name="uq_instruments_source_symbol"),
    )
    op.create_index("ix_instruments_source_active", "instruments", ["source", "is_active"])
    op.create_index("ix_instruments_category", "instruments", ["category"])
    op.create_table(
        "ingestion_states",
        sa.Column("source", sa.String(100), primary_key=True),
        sa.Column("last_count", sa.Integer(), nullable=False),
        sa.Column("high_water_count", sa.Integer(), nullable=False),
        sa.Column("category_counts", sa.JSON(), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("ingestion_states")
    op.drop_index("ix_instruments_category", table_name="instruments")
    op.drop_index("ix_instruments_source_active", table_name="instruments")
    op.drop_table("instruments")
