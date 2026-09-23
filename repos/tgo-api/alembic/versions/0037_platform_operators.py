"""Add independent platform identities; do not enroll existing companies.

Revision ID: 0037_platform_operators
Revises: 0036_retire_personal_wechat
"""

from alembic import op
import sqlalchemy as sa

revision = "0037_platform_operators"
down_revision = "0036_retire_personal_wechat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_platform_operators",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.CheckConstraint("token_version >= 1", name="ck_operator_token_version"),
    )


def downgrade() -> None:
    op.drop_table("api_platform_operators")
