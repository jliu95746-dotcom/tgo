"""Retain operator entitlement adjustments independently of paid history."""

from alembic import op
import sqlalchemy as sa

revision = "0046_operator_authorization"
down_revision = "0045_trial_activation_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_company_accounts",
        sa.Column("operator_override_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_company_accounts", "operator_override_until")
