"""Store one-use trial activation codes without retaining plaintext."""

from alembic import op
import sqlalchemy as sa

revision = "0045_trial_activation_codes"
down_revision = "0044_trial_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_trial_activation_codes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "operator_id", sa.Uuid(),
            sa.ForeignKey("api_platform_operators.id"), nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("redeemed_at", sa.DateTime(timezone=True)),
        sa.Column("redeemed_project_id", sa.Uuid(), sa.ForeignKey("api_projects.id")),
        sa.CheckConstraint(
            "(redeemed_at IS NULL) = (redeemed_project_id IS NULL)",
            name="ck_trial_code_redemption_pair",
        ),
    )


def downgrade() -> None:
    op.drop_table("api_trial_activation_codes")
