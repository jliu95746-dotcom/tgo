"""Track customer notices for failed WeCom media without duplicate sends.

Revision ID: add_wecom_media_failure_notice
Revises: generalize_message_media_envelope
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "add_wecom_media_failure_notice"
down_revision = "generalize_message_media_envelope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pt_wecom_inbox",
        sa.Column("failure_notice_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "pt_wecom_inbox",
        sa.Column(
            "failure_notice_started_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "pt_wecom_inbox",
        sa.Column(
            "failure_notice_finished_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "pt_wecom_inbox",
        sa.Column("failure_notice_error", sa.String(length=80), nullable=True),
    )
    op.create_index(
        "ix_wecom_inbox_failure_notice",
        "pt_wecom_inbox",
        ["platform_id", "failure_notice_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_wecom_inbox_failure_notice", table_name="pt_wecom_inbox")
    op.drop_column("pt_wecom_inbox", "failure_notice_error")
    op.drop_column("pt_wecom_inbox", "failure_notice_finished_at")
    op.drop_column("pt_wecom_inbox", "failure_notice_started_at")
    op.drop_column("pt_wecom_inbox", "failure_notice_status")
