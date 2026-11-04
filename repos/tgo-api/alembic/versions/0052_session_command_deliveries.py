"""Persist manual session command notification intent before external delivery.

Revision ID: 0052_session_command_deliveries
Revises: 0051_visitor_business_scope
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0052_session_command_deliveries"
down_revision = "0051_visitor_business_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_session_command_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visitor_id",
            sa.Uuid(),
            sa.ForeignKey("api_visitors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("api_visitor_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_staff_id",
            sa.Uuid(),
            sa.ForeignKey("api_staff.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_staff_id",
            sa.Uuid(),
            sa.ForeignKey("api_staff.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "to_staff_id", sa.Uuid(), sa.ForeignKey("api_staff.id", ondelete="SET NULL")
        ),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("session_version", sa.DateTime(timezone=False), nullable=False),
        sa.Column("actor_role", sa.String(20), nullable=False),
        sa.Column("actor_token_version", sa.Integer(), nullable=False),
        sa.Column("actor_token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_msg_no", sa.String(100), nullable=False),
        sa.Column("channel_id", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.Integer(), nullable=False),
        sa.Column("from_uid", sa.String(100), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(80)),
        sa.Column("message_id", sa.String(100)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "client_msg_no", name="uq_session_command_delivery_message"
        ),
        sa.CheckConstraint(
            "kind IN ('close','transfer')", name="ck_session_command_delivery_kind"
        ),
        sa.CheckConstraint(
            "status IN ('pending','sending','sent','unknown','skipped')",
            name="ck_session_command_delivery_status",
        ),
    )
    op.create_index(
        "ix_session_command_delivery_recovery",
        "api_session_command_deliveries",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    db = op.get_bind()
    if db.scalar(sa.text("SELECT count(*) FROM api_session_command_deliveries")):
        raise RuntimeError("会话操作通知仍有回执记录，请保留兼容代码回退")
    op.drop_index(
        "ix_session_command_delivery_recovery",
        table_name="api_session_command_deliveries",
    )
    op.drop_table("api_session_command_deliveries")
