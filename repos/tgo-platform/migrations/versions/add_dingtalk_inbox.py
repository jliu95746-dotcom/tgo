"""Add pt_dingtalk_inbox table for DingTalk Bot messages

Revision ID: add_dingtalk_inbox
Revises: add_feishu_inbox
Create Date: 2025-12-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "add_dingtalk_inbox"
down_revision = "add_feishu_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pt_dingtalk_inbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("platform_id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("from_user", sa.String(length=255), nullable=False),
        sa.Column("sender_nick", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column(
            "conversation_type",
            sa.String(length=20),
            server_default="1",
            nullable=False,
        ),
        sa.Column("msg_type", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("session_webhook", sa.Text(), nullable=True),
        sa.Column("session_webhook_expired_time", sa.BigInteger(), nullable=True),
        sa.Column("ai_reply", sa.Text(), nullable=True),
        sa.Column(
            "raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default="pending", nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["platform_id"], ["pt_platforms.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform_id", "message_id", name="uq_dingtalk_inbox_platform_message"
        ),
    )
    op.create_index(
        "ix_pt_dingtalk_inbox_platform_id",
        "pt_dingtalk_inbox",
        ["platform_id"],
        unique=False,
    )
    op.create_index(
        "ix_pt_dingtalk_inbox_conversation_id",
        "pt_dingtalk_inbox",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_dingtalk_inbox_platform_status",
        "pt_dingtalk_inbox",
        ["platform_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_dingtalk_inbox_status_fetched",
        "pt_dingtalk_inbox",
        ["status", "fetched_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dingtalk_inbox_status_fetched", table_name="pt_dingtalk_inbox")
    op.drop_index("ix_dingtalk_inbox_platform_status", table_name="pt_dingtalk_inbox")
    op.drop_index(
        "ix_pt_dingtalk_inbox_conversation_id", table_name="pt_dingtalk_inbox"
    )
    op.drop_index("ix_pt_dingtalk_inbox_platform_id", table_name="pt_dingtalk_inbox")
    op.drop_table("pt_dingtalk_inbox")
