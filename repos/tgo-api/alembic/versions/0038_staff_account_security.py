"""Separate login eligibility from reception and allow token revocation.

Existing accounts remain enabled; no company is enrolled in a trial.
"""

from alembic import op
import sqlalchemy as sa

revision = "0038_staff_account_security"
down_revision = "0037_platform_operators"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_staff",
        sa.Column(
            "account_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "api_staff",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "api_staff",
        sa.Column(
            "email_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_staff_token_version",
        "api_staff",
        "token_version >= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_staff_token_version", "api_staff", type_="check")
    op.drop_column("api_staff", "email_verified_at")
    op.drop_column("api_staff", "token_version")
    op.drop_column("api_staff", "account_enabled")
