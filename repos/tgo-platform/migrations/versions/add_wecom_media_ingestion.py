"""Add durable WeCom media ingestion state.

Revision ID: add_wecom_media_ingestion
Revises: fix_wecom_reliability_state
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "add_wecom_media_ingestion"
down_revision = "fix_wecom_reliability_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pt_message_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_media_id", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("storage_provider", sa.String(length=50), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("declared_size", sa.BigInteger(), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("encryption_mode", sa.String(length=50), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=100), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["platform_id"], ["pt_platforms.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["inbox_id"], ["pt_wecom_inbox.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "inbox_id",
            name="uq_message_media_inbox",
        ),
    )
    op.create_index(
        "ix_pt_message_media_platform_id",
        "pt_message_media",
        ["platform_id"],
        unique=False,
    )
    op.create_index(
        "ix_pt_message_media_inbox_id",
        "pt_message_media",
        ["inbox_id"],
        unique=False,
    )
    op.create_index(
        "ix_message_media_status_created",
        "pt_message_media",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_message_media_status_retention",
        "pt_message_media",
        ["status", "retention_until"],
        unique=False,
    )

    op.create_table(
        "pt_media_processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=20), server_default="download", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("staging_object_key", sa.Text(), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["media_id"], ["pt_message_media.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_id", "job_type", name="uq_media_processing_job_type"
        ),
    )
    op.create_index(
        "ix_pt_media_processing_jobs_media_id",
        "pt_media_processing_jobs",
        ["media_id"],
        unique=False,
    )
    op.create_index(
        "ix_pt_media_processing_jobs_lease_expires_at",
        "pt_media_processing_jobs",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_pt_media_processing_jobs_next_attempt_at",
        "pt_media_processing_jobs",
        ["next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_media_processing_job_status_attempt",
        "pt_media_processing_jobs",
        ["status", "next_attempt_at"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO pt_message_media (
            id, platform_id, inbox_id, source_media_id, media_type, status,
            original_filename, declared_size
        )
        SELECT
            md5(inbox.id::text || ':' || media.source_media_id)::uuid,
            inbox.platform_id,
            inbox.id,
            media.source_media_id,
            inbox.msg_type,
            CASE
                WHEN inbox.msg_type IN ('image', 'voice') THEN 'pending'
                ELSE 'unsupported'
            END,
            CASE WHEN inbox.msg_type = 'file'
                THEN left(
                    inbox.raw_payload #>> '{kf_sync_msg,file,file_name}',
                    255
                )
                ELSE NULL
            END,
            CASE WHEN inbox.msg_type = 'file'
                THEN CASE
                    WHEN (inbox.raw_payload #>> '{kf_sync_msg,file,file_size}')
                        ~ '^[0-9]{1,19}$'
                    THEN CASE
                        WHEN (
                            inbox.raw_payload #>> '{kf_sync_msg,file,file_size}'
                        )::numeric <= 9223372036854775807
                        THEN (
                            inbox.raw_payload #>> '{kf_sync_msg,file,file_size}'
                        )::bigint
                        ELSE NULL
                    END
                    ELSE NULL
                END
                ELSE NULL
            END
        FROM pt_wecom_inbox inbox
        CROSS JOIN LATERAL (
            SELECT CASE inbox.msg_type
                WHEN 'image' THEN inbox.raw_payload #>> '{kf_sync_msg,image,media_id}'
                WHEN 'voice' THEN inbox.raw_payload #>> '{kf_sync_msg,voice,media_id}'
                WHEN 'video' THEN inbox.raw_payload #>> '{kf_sync_msg,video,media_id}'
                WHEN 'file' THEN inbox.raw_payload #>> '{kf_sync_msg,file,media_id}'
                ELSE NULL
            END AS source_media_id
        ) media
        WHERE inbox.status = 'pending_media'
          AND inbox.msg_type IN ('image', 'voice', 'video', 'file')
          AND COALESCE(media.source_media_id, '') <> ''
          AND length(media.source_media_id) <= 255
        ON CONFLICT (inbox_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO pt_media_processing_jobs (id, media_id, job_type, status)
        SELECT md5(media.id::text || chr(58) || 'download')::uuid,
               media.id, 'download', 'pending'
        FROM pt_message_media media
        WHERE media.media_type IN ('image', 'voice')
        ON CONFLICT (media_id, job_type) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE pt_wecom_inbox
        SET status = 'unsupported_media'
        WHERE status = 'pending_media'
          AND msg_type NOT IN ('image', 'voice')
        """
    )
    op.execute(
        """
        UPDATE pt_wecom_inbox inbox
        SET status = 'media_failed',
            error_message = 'WeCom media message is missing media_id'
        WHERE inbox.status = 'pending_media'
          AND inbox.msg_type IN ('image', 'voice')
          AND NOT EXISTS (
              SELECT 1 FROM pt_message_media media WHERE media.inbox_id = inbox.id
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE pt_wecom_inbox
        SET status = 'pending_media', error_message = NULL
        WHERE status IN (
            'media_downloading', 'media_downloaded', 'media_failed',
            'unsupported_media'
        )
        """
    )
    op.drop_index(
        "ix_media_processing_job_status_attempt",
        table_name="pt_media_processing_jobs",
    )
    op.drop_index(
        "ix_pt_media_processing_jobs_next_attempt_at",
        table_name="pt_media_processing_jobs",
    )
    op.drop_index(
        "ix_pt_media_processing_jobs_lease_expires_at",
        table_name="pt_media_processing_jobs",
    )
    op.drop_index(
        "ix_pt_media_processing_jobs_media_id",
        table_name="pt_media_processing_jobs",
    )
    op.drop_table("pt_media_processing_jobs")

    # Some development databases applied an early draft before this index existed.
    op.execute("DROP INDEX IF EXISTS ix_message_media_status_retention")
    op.drop_index("ix_message_media_status_created", table_name="pt_message_media")
    op.drop_index("ix_pt_message_media_inbox_id", table_name="pt_message_media")
    op.drop_index("ix_pt_message_media_platform_id", table_name="pt_message_media")
    op.drop_table("pt_message_media")
