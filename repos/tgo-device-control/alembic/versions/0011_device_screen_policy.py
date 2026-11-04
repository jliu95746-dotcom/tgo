"""Persist screen scope and revocation tombstones, never connection confirmations.

Revision ID: 0011_device_screen_policy
Revises: 0010_device_workflow_execution
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011_device_screen_policy"
down_revision = "0010_device_workflow_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dc_device_screen_policies",
        sa.Column(
            "device_id",
            UUID(as_uuid=True),
            sa.ForeignKey("dc_devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("connection_instance_id", sa.String(32), nullable=False),
        sa.Column("grant_id", sa.String(32), nullable=False),
        sa.Column("max_actions", sa.Integer(), nullable=False),
        sa.Column("allow_click", sa.Boolean(), nullable=False),
        sa.Column("allow_text", sa.Boolean(), nullable=False),
        sa.Column("click_regions", JSONB(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision BETWEEN 1 AND 2147483647", name="ck_dc_screen_revision"
        ),
        sa.CheckConstraint("max_actions BETWEEN 1 AND 50", name="ck_dc_screen_actions"),
    )
    op.create_index(
        "ix_dc_device_screen_policies_project_id",
        "dc_device_screen_policies",
        ["project_id"],
    )


def downgrade() -> None:
    op.execute("LOCK TABLE dc_device_screen_policies IN ACCESS EXCLUSIVE MODE")
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM dc_device_screen_policies)"))
        .scalar()
    ):
        raise RuntimeError("Refusing to discard screen policy or revocation history")
    op.drop_index(
        "ix_dc_device_screen_policies_project_id",
        table_name="dc_device_screen_policies",
    )
    op.drop_table("dc_device_screen_policies")
