"""Store daily verified statement summaries and reconciliation issues."""

from alembic import op
import sqlalchemy as sa

revision = "0043_billing_reconciliation"
down_revision = "0042_ai_usage_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("api_billing_reconciliations",
        sa.Column("bill_date", sa.Date(), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("issue_count", sa.Integer(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade() -> None:
    op.drop_table("api_billing_reconciliations")
