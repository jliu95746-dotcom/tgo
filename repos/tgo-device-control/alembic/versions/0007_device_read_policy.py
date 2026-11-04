"""Persist desired device read scope; applied state lives on each connection.

Revision ID: 0007_device_read_policy
Revises: 0006_device_scenario_drafts
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0007_device_read_policy"
down_revision = "0006_device_scenario_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dc_device_read_policies",
        sa.Column(
            "device_id",
            UUID(as_uuid=True),
            sa.ForeignKey("dc_devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("allowed_directory", sa.String(1024), nullable=False),
        sa.Column("max_lines", sa.Integer(), nullable=False),
        sa.Column("max_offset", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_dc_device_read_policies_project_id",
        "dc_device_read_policies",
        ["project_id"],
    )


def downgrade() -> None:
    op.execute("LOCK TABLE dc_device_read_policies IN ACCESS EXCLUSIVE MODE")
    has_policies = (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM dc_device_read_policies)"))
        .scalar()
    )
    if has_policies:
        raise RuntimeError("Refusing to remove non-empty device read policies")
    op.drop_index(
        "ix_dc_device_read_policies_project_id",
        table_name="dc_device_read_policies",
    )
    op.drop_table("dc_device_read_policies")
