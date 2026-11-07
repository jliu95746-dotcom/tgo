"""Add source_type column to pt_wecom_inbox table

Revision ID: add_wecom_inbox_source_type
Revises: 539a0c191ecf
Create Date: 2025-12-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_wecom_inbox_source_type"
down_revision = "539a0c191ecf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add source_type column to distinguish between wecom_kf (客服) and wecom_bot (群机器人)
    op.add_column(
        "pt_wecom_inbox",
        sa.Column(
            "source_type",
            sa.String(length=20),
            server_default="wecom_kf",
            nullable=False,
        ),
    )
    # Create index on source_type for efficient filtering
    op.create_index(
        "ix_pt_wecom_inbox_source_type", "pt_wecom_inbox", ["source_type"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_pt_wecom_inbox_source_type", table_name="pt_wecom_inbox")
    op.drop_column("pt_wecom_inbox", "source_type")
