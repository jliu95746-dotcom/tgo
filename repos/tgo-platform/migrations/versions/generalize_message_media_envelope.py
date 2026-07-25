"""Generalize WeCom media metadata to every inbound channel.

Revision ID: generalize_message_media_envelope
Revises: add_global_inbox_reliability
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "generalize_message_media_envelope"
down_revision = "add_global_inbox_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pt_message_media",
        sa.Column(
            "source_channel",
            sa.String(length=32),
            server_default="wecom",
            nullable=False,
        ),
    )
    op.add_column(
        "pt_message_media",
        sa.Column("source_message_id", sa.String(length=255), nullable=True),
    )
    op.execute(
        "UPDATE pt_message_media media "
        "SET source_message_id = inbox.message_id "
        "FROM pt_wecom_inbox inbox "
        "WHERE media.inbox_id = inbox.id "
        "AND media.source_message_id IS NULL"
    )
    op.alter_column(
        "pt_message_media",
        "source_message_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.drop_constraint(
        "pt_message_media_inbox_id_fkey",
        "pt_message_media",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_message_media_inbox",
        "pt_message_media",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_message_media_channel_inbox",
        "pt_message_media",
        ["source_channel", "inbox_id"],
    )
    op.create_index(
        "ix_pt_message_media_source_channel",
        "pt_message_media",
        ["source_channel"],
    )
    op.create_index(
        "ix_pt_message_media_source_message_id",
        "pt_message_media",
        ["source_message_id"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pt_message_media
                WHERE source_channel <> 'wecom'
            ) THEN
                RAISE EXCEPTION
                    'Cannot restore WeCom-only media FK: non-WeCom rows exist';
            END IF;
        END $$;
        """
    )
    op.drop_index(
        "ix_pt_message_media_source_message_id",
        table_name="pt_message_media",
    )
    op.drop_index(
        "ix_pt_message_media_source_channel",
        table_name="pt_message_media",
    )
    op.drop_constraint(
        "uq_message_media_channel_inbox",
        "pt_message_media",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_message_media_inbox",
        "pt_message_media",
        ["inbox_id"],
    )
    op.create_foreign_key(
        "pt_message_media_inbox_id_fkey",
        "pt_message_media",
        "pt_wecom_inbox",
        ["inbox_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("pt_message_media", "source_message_id")
    op.drop_column("pt_message_media", "source_channel")
