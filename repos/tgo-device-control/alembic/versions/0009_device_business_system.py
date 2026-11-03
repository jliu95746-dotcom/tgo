"""Add an optional business system name to device scenario drafts.

Revision ID: 0009_device_business_system
Revises: 0008_device_read_policy_test
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_device_business_system"
down_revision = "0008_device_read_policy_test"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dc_device_scenario_drafts",
        sa.Column("business_system", sa.String(120), nullable=True),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE dc_device_scenario_drafts IN ACCESS EXCLUSIVE MODE")
    has_names = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM dc_device_scenario_drafts "
            "WHERE business_system IS NOT NULL)"
        )
    ).scalar()
    if has_names:
        raise RuntimeError("Refusing to remove named device business systems")
    op.drop_column("dc_device_scenario_drafts", "business_system")
