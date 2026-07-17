"""Fix WeCom callback deduplication and persist sync cursors.

Revision ID: fix_wecom_reliability_state
Revises: add_wecom_reliability_state
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "fix_wecom_reliability_state"
down_revision = "add_wecom_reliability_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pt_wecom_sync_jobs",
        sa.Column("callback_fingerprint", sa.String(length=64), nullable=True),
    )
    op.execute(
        "UPDATE pt_wecom_sync_jobs "
        "SET callback_fingerprint = md5(platform_id::text || ':' || id::text) "
        "WHERE callback_fingerprint IS NULL"
    )
    op.alter_column(
        "pt_wecom_sync_jobs",
        "callback_fingerprint",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_constraint(
        "uq_wecom_sync_job_event",
        "pt_wecom_sync_jobs",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_wecom_sync_job_callback",
        "pt_wecom_sync_jobs",
        ["platform_id", "callback_fingerprint"],
    )

    op.create_table(
        "pt_wecom_sync_cursors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("open_kfid", sa.String(length=128), nullable=False),
        sa.Column("cursor", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["platform_id"],
            ["pt_platforms.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform_id",
            "open_kfid",
            name="uq_wecom_sync_cursor_account",
        ),
    )
    op.create_index(
        "ix_pt_wecom_sync_cursors_platform_id",
        "pt_wecom_sync_cursors",
        ["platform_id"],
        unique=False,
    )

    op.execute(
        "UPDATE pt_wecom_inbox "
        "SET status = 'pending', processing_started_at = NULL, "
        "lease_expires_at = NULL, next_attempt_at = NULL "
        "WHERE status = 'processing'"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pt_wecom_sync_cursors_platform_id",
        table_name="pt_wecom_sync_cursors",
    )
    op.drop_table("pt_wecom_sync_cursors")

    op.drop_constraint(
        "uq_wecom_sync_job_callback",
        "pt_wecom_sync_jobs",
        type_="unique",
    )
    op.execute(
        "DELETE FROM pt_wecom_sync_jobs WHERE id IN ("
        "SELECT id FROM ("
        "SELECT id, row_number() OVER ("
        "PARTITION BY platform_id, open_kfid, event_token "
        "ORDER BY created_at, id"
        ") AS duplicate_number FROM pt_wecom_sync_jobs"
        ") duplicates WHERE duplicate_number > 1)"
    )
    op.create_unique_constraint(
        "uq_wecom_sync_job_event",
        "pt_wecom_sync_jobs",
        ["platform_id", "open_kfid", "event_token"],
    )
    op.drop_column("pt_wecom_sync_jobs", "callback_fingerprint")
