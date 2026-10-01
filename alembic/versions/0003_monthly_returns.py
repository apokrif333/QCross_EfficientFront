"""monthly return series revisions and import checkpoints

Revision ID: 0003_monthly_returns
Revises: 0002_currency_availability
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_monthly_returns"
down_revision = "0002_currency_availability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "return_imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_imports")),
        sa.UniqueConstraint(
            "instrument_id", "source", "currency", name="uq_return_import_identity"
        ),
    )
    op.create_index(
        op.f("ix_return_imports_instrument_id"), "return_imports", ["instrument_id"], unique=False
    )
    op.create_table(
        "return_series",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("frequency", sa.String(length=20), nullable=False),
        sa.Column("return_type", sa.String(length=50), nullable=False),
        sa.Column("is_extended_history", sa.Boolean(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_series")),
        sa.UniqueConstraint(
            "instrument_id",
            "source",
            "currency",
            "frequency",
            "return_type",
            name="uq_return_series_identity",
        ),
    )
    op.create_index(
        op.f("ix_return_series_instrument_id"), "return_series", ["instrument_id"], unique=False
    )
    op.create_table(
        "monthly_returns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "return_value", sa.Numeric(38, 18).with_variant(sa.String(64), "sqlite"), nullable=False
        ),
        sa.Column("quality_flag", sa.String(length=100), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["return_series.id"],
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monthly_returns")),
        sa.UniqueConstraint("series_id", "date", name="uq_monthly_returns_date"),
    )
    op.create_index(
        op.f("ix_monthly_returns_series_id"), "monthly_returns", ["series_id"], unique=False
    )
    op.create_table(
        "return_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "old_value", sa.Numeric(38, 18).with_variant(sa.String(64), "sqlite"), nullable=False
        ),
        sa.Column(
            "new_value", sa.Numeric(38, 18).with_variant(sa.String(64), "sqlite"), nullable=False
        ),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["return_series.id"],
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_revisions")),
        sa.UniqueConstraint(
            "series_id", "date", "old_value", "new_value", name="uq_return_revision_values"
        ),
    )
    op.create_index(
        op.f("ix_return_revisions_series_id"), "return_revisions", ["series_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_return_revisions_series_id"), table_name="return_revisions")
    op.drop_table("return_revisions")
    op.drop_index(op.f("ix_monthly_returns_series_id"), table_name="monthly_returns")
    op.drop_table("monthly_returns")
    op.drop_index(op.f("ix_return_series_instrument_id"), table_name="return_series")
    op.drop_table("return_series")
    op.drop_index(op.f("ix_return_imports_instrument_id"), table_name="return_imports")
    op.drop_table("return_imports")
