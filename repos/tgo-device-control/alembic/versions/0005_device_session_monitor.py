"""Persist device execution lifecycle and metadata-only steps.

Revision ID: 0005_device_session_monitor
Revises: 0004_rm_device_model_config
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_device_session_monitor"
down_revision = "0004_rm_device_model_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy rows have no reliable completion evidence. Do not invent success.
    op.add_column(
        "dc_sessions",
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="interrupted",
        ),
    )
    op.add_column(
        "dc_sessions", sa.Column("agent_name", sa.String(255), nullable=True)
    )
    op.add_column(
        "dc_sessions",
        sa.Column(
            "lease_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "dc_sessions",
        sa.Column(
            "failed_actions_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_table(
        "dc_session_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("dc_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_dc_session_steps_session_id", "dc_session_steps", ["session_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dc_session_steps_session_id", table_name="dc_session_steps"
    )
    op.drop_table("dc_session_steps")
    for name in (
        "failed_actions_count",
        "lease_expires_at",
        "agent_name",
        "status",
    ):
        op.drop_column("dc_sessions", name)
