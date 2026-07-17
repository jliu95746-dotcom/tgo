"""Add durable WeCom sync jobs and processing leases.

Revision ID: add_wecom_reliability_state
Revises: add_slack_inbox
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "add_wecom_reliability_state"
down_revision = "add_slack_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pt_wecom_inbox",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pt_wecom_inbox",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pt_wecom_inbox",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_pt_wecom_inbox_lease_expires_at",
        "pt_wecom_inbox",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_pt_wecom_inbox_next_attempt_at",
        "pt_wecom_inbox",
        ["next_attempt_at"],
        unique=False,
    )
    op.create_table(
        "pt_wecom_sync_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "platform_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("event_token", sa.String(length=128), nullable=False),
        sa.Column("open_kfid", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["platform_id"],
            ["pt_platforms.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform_id",
            "open_kfid",
            "event_token",
            name="uq_wecom_sync_job_event",
        ),
    )
    op.create_index(
        "ix_pt_wecom_sync_jobs_platform_id",
        "pt_wecom_sync_jobs",
        ["platform_id"],
        unique=False,
    )

    op.create_index(
        "ix_pt_wecom_sync_jobs_lease_expires_at",
        "pt_wecom_sync_jobs",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_pt_wecom_sync_jobs_next_attempt_at",
        "pt_wecom_sync_jobs",
        ["next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_wecom_sync_job_platform_status",
        "pt_wecom_sync_jobs",
        ["platform_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wecom_sync_job_platform_status",
        table_name="pt_wecom_sync_jobs",
    )
    op.drop_index(
        "ix_pt_wecom_sync_jobs_next_attempt_at",
        table_name="pt_wecom_sync_jobs",
    )
    op.drop_index(
        "ix_pt_wecom_sync_jobs_lease_expires_at",
        table_name="pt_wecom_sync_jobs",
    )
    op.drop_index(
        "ix_pt_wecom_sync_jobs_platform_id",
        table_name="pt_wecom_sync_jobs",
    )
    op.drop_table("pt_wecom_sync_jobs")

    op.drop_index(
        "ix_pt_wecom_inbox_next_attempt_at",
        table_name="pt_wecom_inbox",
    )
    op.drop_index(
        "ix_pt_wecom_inbox_lease_expires_at",
        table_name="pt_wecom_inbox",
    )
    op.drop_column("pt_wecom_inbox", "next_attempt_at")
    op.drop_column("pt_wecom_inbox", "lease_expires_at")
    op.drop_column("pt_wecom_inbox", "processing_started_at")
