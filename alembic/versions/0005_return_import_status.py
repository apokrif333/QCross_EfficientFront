"""Allow descriptive pair statuses on PostgreSQL as well as SQLite."""

import sqlalchemy as sa

from alembic import op

revision = "0005_return_import_status"
down_revision = "0004_backtester_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("return_imports") as batch:
        batch.alter_column("status", existing_type=sa.String(20), type_=sa.String(40))


def downgrade() -> None:
    # Keep long statuses readable until an older application takes over.
    op.execute("UPDATE return_imports SET status = 'failed' WHERE length(status) > 20")
    with op.batch_alter_table("return_imports") as batch:
        batch.alter_column("status", existing_type=sa.String(40), type_=sa.String(20))
