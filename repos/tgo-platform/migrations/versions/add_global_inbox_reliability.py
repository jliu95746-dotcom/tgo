"""Add durable processing leases to every channel Inbox.

Revision ID: add_global_inbox_reliability
Revises: add_wecom_media_ingestion
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "add_global_inbox_reliability"
down_revision = "add_wecom_media_ingestion"
branch_labels = None
depends_on = None


INBOX_TABLES = (
    "pt_email_inbox",
    "pt_wukongim_inbox",
    "pt_feishu_inbox",
    "pt_dingtalk_inbox",
    "pt_telegram_inbox",
    "pt_slack_inbox",
)


def upgrade() -> None:
    for table_name in INBOX_TABLES:
        op.add_column(
            table_name,
            sa.Column(
                "processing_started_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "lease_expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "next_attempt_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.create_index(
            f"ix_{table_name}_lease_expires_at",
            table_name,
            ["lease_expires_at"],
        )
        op.create_index(
            f"ix_{table_name}_next_attempt_at",
            table_name,
            ["next_attempt_at"],
        )
        op.execute(
            f"UPDATE {table_name} SET status = 'pending' "
            "WHERE status = 'processing'"
        )


def downgrade() -> None:
    for table_name in reversed(INBOX_TABLES):
        op.drop_index(
            f"ix_{table_name}_next_attempt_at",
            table_name=table_name,
        )
        op.drop_index(
            f"ix_{table_name}_lease_expires_at",
            table_name=table_name,
        )
        op.drop_column(table_name, "next_attempt_at")
        op.drop_column(table_name, "lease_expires_at")
        op.drop_column(table_name, "processing_started_at")
