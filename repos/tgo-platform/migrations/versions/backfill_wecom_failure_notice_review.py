"""Prevent automatic late replies to historical WeCom media failures.

Revision ID: backfill_wecom_failure_notice_review
Revises: add_wecom_media_failure_notice
"""

from __future__ import annotations

from alembic import op


revision = "backfill_wecom_failure_notice_review"
down_revision = "add_wecom_media_failure_notice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rows predating the notifier have no proof that a customer message would
    # still be timely or even safe. Operators reconcile them before any reply.
    op.execute(
        "UPDATE pt_wecom_inbox "
        "SET failure_notice_status = 'needs_attention', "
        "failure_notice_error = 'legacy failure requires review' "
        "WHERE source_type = 'wecom_kf' AND is_from_colleague = false "
        "AND (status IN ('media_failed', 'unsupported_media') "
        "OR (status = 'dead' AND msg_type IN ('image', 'voice'))) "
        "AND failure_notice_status IS NULL"
    )


def downgrade() -> None:
    # Keep the review marker. Clearing it would let a later upgrade send a
    # duplicate or obsolete customer notice.
    pass
