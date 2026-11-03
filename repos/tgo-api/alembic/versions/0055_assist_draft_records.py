"""Retain staff-visible assist drafts and link confirmed staff replies.

Revision ID: 0055_assist_draft_records
Revises: 0054_shadow_reply_link
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0055_assist_draft_records"
down_revision = "0054_shadow_reply_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_assist_draft_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("visitor_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.String(255), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("response_data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["staff_id"], ["api_staff.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["visitor_id"], ["api_visitors.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "project_id", "staff_id", "request_id", name="uq_assist_draft_staff_request"
        ),
    )
    op.create_index(
        "ix_assist_draft_project_visitor_source",
        "api_assist_draft_records",
        ["project_id", "visitor_id", "source_message_id"],
    )
    op.add_column(
        "api_staff_message_deliveries",
        sa.Column("assist_draft_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_staff_delivery_assist_draft",
        "api_staff_message_deliveries",
        "api_assist_draft_records",
        ["assist_draft_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_drafts = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM api_assist_draft_records)")
    )
    has_links = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM api_staff_message_deliveries "
            "WHERE assist_draft_id IS NOT NULL)"
        )
    )
    if has_drafts or has_links:
        raise RuntimeError("AI 草稿对照已有记录，先完成受控导出和数据迁移后再回退")
    op.drop_constraint(
        "fk_staff_delivery_assist_draft",
        "api_staff_message_deliveries",
        type_="foreignkey",
    )
    op.drop_column("api_staff_message_deliveries", "assist_draft_id")
    op.drop_index(
        "ix_assist_draft_project_visitor_source", table_name="api_assist_draft_records"
    )
    op.drop_table("api_assist_draft_records")
