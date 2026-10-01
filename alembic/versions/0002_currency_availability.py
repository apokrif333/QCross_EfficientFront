"""Expose source-provided currency availability alongside instrument currency."""

import sqlalchemy as sa

from alembic import op

revision = "0002_currency_availability"
down_revision = "0001_instruments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("instruments", sa.Column("available_currencies", sa.JSON(none_as_null=True)))
    op.create_index("ix_instruments_currency", "instruments", ["currency"])


def downgrade() -> None:
    op.drop_index("ix_instruments_currency", table_name="instruments")
    with op.batch_alter_table("instruments") as batch:
        batch.drop_column("available_currencies")
