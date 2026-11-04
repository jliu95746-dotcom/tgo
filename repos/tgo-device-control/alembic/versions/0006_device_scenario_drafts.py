"""Store tenant-owned device scenario drafts without granting execution rights.

Revision ID: 0006_device_scenario_drafts
Revises: 0005_device_session_monitor
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0006_device_scenario_drafts"
down_revision = "0005_device_session_monitor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dc_device_scenario_drafts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            UUID(as_uuid=True),
            sa.ForeignKey("dc_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("scenario", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(255), nullable=False),
        sa.Column("target_directory", sa.String(1024), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "device_id", "scenario", name="uq_dc_device_scenario_draft"
        ),
    )
    op.create_index(
        "ix_dc_device_scenario_drafts_device_id",
        "dc_device_scenario_drafts",
        ["device_id"],
    )
    op.create_index(
        "ix_dc_device_scenario_drafts_project_id",
        "dc_device_scenario_drafts",
        ["project_id"],
    )


def downgrade() -> None:
    op.execute("LOCK TABLE dc_device_scenario_drafts IN ACCESS EXCLUSIVE MODE")
    has_drafts = op.get_bind().execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM dc_device_scenario_drafts)")
    ).scalar()
    if has_drafts:
        raise RuntimeError(
            "Refusing to remove non-empty device scenario drafts"
        )
    op.drop_index(
        "ix_dc_device_scenario_drafts_project_id",
        table_name="dc_device_scenario_drafts",
    )
    op.drop_index(
        "ix_dc_device_scenario_drafts_device_id", table_name="dc_device_scenario_drafts"
    )
    op.drop_table("dc_device_scenario_drafts")
