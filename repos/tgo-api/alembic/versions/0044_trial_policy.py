"""Store operator trial settings without revising existing enterprise grants."""

from alembic import op
import sqlalchemy as sa

revision = "0044_trial_policy"
down_revision = "0043_billing_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_trial_policies",
        sa.Column("version", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("ai_replies", sa.Integer(), nullable=False),
        sa.Column(
            "operator_id",
            sa.Uuid(),
            sa.ForeignKey("api_platform_operators.id"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("version > 0", name="ck_trial_policy_version"),
        sa.CheckConstraint(
            "ai_replies >= 0 AND ai_replies <= 100000", name="ck_trial_policy_replies"
        ),
    )


def downgrade() -> None:
    op.drop_table("api_trial_policies")
