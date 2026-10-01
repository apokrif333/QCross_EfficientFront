"""Record extraction method and currency provenance without duplicating series."""

import sqlalchemy as sa

from alembic import op

revision = "0004_backtester_provenance"
down_revision = "0003_monthly_returns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "return_series",
        sa.Column(
            "extraction_method", sa.String(30), nullable=False, server_default="individual_page"
        ),
    )
    op.add_column(
        "return_series",
        sa.Column("currency_kind", sa.String(20), nullable=False, server_default="native"),
    )
    op.add_column(
        "return_series",
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="validated"),
    )
    op.execute("UPDATE return_imports SET status = 'completed' WHERE status = 'successful'")
    op.execute(
        "UPDATE return_imports SET status = 'pending' "
        "WHERE status IN ('unsupported', 'unresolved', 'not_attempted')"
    )


def downgrade() -> None:
    op.execute("UPDATE return_imports SET status = 'successful' WHERE status = 'completed'")
    op.drop_column("return_series", "validation_status")
    op.drop_column("return_series", "currency_kind")
    op.drop_column("return_series", "extraction_method")
