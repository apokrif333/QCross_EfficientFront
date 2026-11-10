"""Versioned BIL reference and derived inflation; nominal tables unchanged."""

import sqlalchemy as sa

from alembic import op
from app.db.models.returns import ExactReturn

revision = "0006_macro_references"
down_revision = "0005_return_import_status"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reference_series",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("series_code", sa.String(80), nullable=False, unique=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("units", sa.String(40), nullable=False),
        sa.Column("return_series_id", sa.Integer(), sa.ForeignKey("return_series.id")),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_version", sa.String(64), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
    )
    op.create_table(
        "reference_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("series_id", sa.Integer(), sa.ForeignKey("reference_series.id"), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("value", ExactReturn(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("source_snapshot", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("series_id", "month"),
    )
    op.create_index("ix_reference_observations_series_id", "reference_observations", ["series_id"])
    op.create_table(
        "reference_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("series_id", sa.Integer(), sa.ForeignKey("reference_series.id"), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("old_value", ExactReturn(), nullable=False),
        sa.Column("new_value", ExactReturn(), nullable=False),
        sa.Column("old_status", sa.String(40), nullable=False),
        sa.Column("new_status", sa.String(40), nullable=False),
        sa.Column("source_snapshot", sa.String(64), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("reference_revisions")
    op.drop_index("ix_reference_observations_series_id", table_name="reference_observations")
    op.drop_table("reference_observations")
    op.drop_table("reference_series")
