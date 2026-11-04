"""Persist reviewed script publication and revocation history.

Revision ID: 0013_device_script_policy
Revises: 0012_device_read_revocation
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_device_script_policy"
down_revision = "0012_device_read_revocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dc_device_script_policies",
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dc_devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("script_id", sa.String(48), nullable=False),
        sa.Column("grant_digest", sa.String(64), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_output_bytes", sa.Integer(), nullable=False),
        sa.Column("max_runs", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "revision BETWEEN 1 AND 2147483647", name="ck_dc_script_revision"
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 120", name="ck_dc_script_timeout"
        ),
        sa.CheckConstraint(
            "max_output_bytes BETWEEN 256 AND 65536",
            name="ck_dc_script_output",
        ),
        sa.CheckConstraint(
            "max_runs BETWEEN 1 AND 100", name="ck_dc_script_runs"
        ),
        sa.CheckConstraint(
            "enabled = (revoked_at IS NULL)", name="ck_dc_script_revocation"
        ),
    )
    op.create_index(
        "ix_dc_device_script_policies_project_id",
        "dc_device_script_policies",
        ["project_id"],
    )


def downgrade() -> None:
    op.execute("LOCK TABLE dc_device_script_policies IN ACCESS EXCLUSIVE MODE")
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM dc_device_script_policies)")
        )
        .scalar()
    ):
        raise RuntimeError(
            "Refusing to discard script policy publication history"
        )
    op.drop_index(
        "ix_dc_device_script_policies_project_id",
        table_name="dc_device_script_policies",
    )
    op.drop_table("dc_device_script_policies")
