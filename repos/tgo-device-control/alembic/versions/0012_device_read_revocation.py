"""Retain read-policy revocation history for explicit device mode changes.

Revision ID: 0012_device_read_revocation
Revises: 0011_device_screen_policy
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_device_read_revocation"
down_revision = "0011_device_screen_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dc_device_read_policies",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "dc_device_read_policies",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE dc_device_read_policies IN ACCESS EXCLUSIVE MODE")
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM dc_device_read_policies "
                "WHERE enabled=false OR revoked_at IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Refusing to discard read policy revocation history")
    op.drop_column("dc_device_read_policies", "revoked_at")
    op.drop_column("dc_device_read_policies", "enabled")
