"""Keep metadata-only read test receipts bound to one device connection.

Revision ID: 0008_device_read_policy_test
Revises: 0007_device_read_policy
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_device_read_policy_test"
down_revision = "0007_device_read_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dc_device_read_policies",
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dc_device_read_policies",
        sa.Column("last_test_status", sa.String(16), nullable=True),
    )
    op.add_column(
        "dc_device_read_policies",
        sa.Column("last_test_digest", sa.String(64), nullable=True),
    )
    op.add_column(
        "dc_device_read_policies",
        sa.Column("last_test_connection_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE dc_device_read_policies IN ACCESS EXCLUSIVE MODE")
    has_results = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM dc_device_read_policies "
                "WHERE last_tested_at IS NOT NULL)"
            )
        )
        .scalar()
    )
    if has_results:
        raise RuntimeError("Refusing to remove device read test receipts")
    for column in (
        "last_test_connection_id",
        "last_test_digest",
        "last_test_status",
        "last_tested_at",
    ):
        op.drop_column("dc_device_read_policies", column)
