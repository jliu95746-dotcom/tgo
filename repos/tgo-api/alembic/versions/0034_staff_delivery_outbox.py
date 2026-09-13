"""Add durable staff delivery receipts and recoverable history.

Revision ID: 0034_staff_delivery_outbox
Revises: 0033_visitor_service_mode
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0034_staff_delivery_outbox"
down_revision = "0033_visitor_service_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_staff_message_deliveries",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("api_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "staff_id",
            sa.UUID(),
            sa.ForeignKey("api_staff.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visitor_id",
            sa.UUID(),
            sa.ForeignKey("api_visitors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel_id", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.Integer(), nullable=False),
        sa.Column("client_msg_no", sa.String(100), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("external_status", sa.String(20), nullable=False),
        sa.Column("history_status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("history_error", sa.String(100)),
        sa.Column("history_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "staff_id", "client_msg_no", name="uq_staff_delivery_identity"
        ),
        sa.CheckConstraint(
            "external_status IN ('not_required','sending','sent','failed','unknown')",
            name="ck_staff_delivery_external",
        ),
        sa.CheckConstraint(
            "history_status IN ('pending','sent')", name="ck_staff_delivery_history"
        ),
    )
    op.create_index(
        "ix_staff_delivery_recovery",
        "api_staff_message_deliveries",
        ["history_status", "external_status", "next_retry_at"],
    )


def downgrade() -> None:
    # Operators must drain/export pending receipts before an explicit downgrade.
    op.drop_index(
        "ix_staff_delivery_recovery", table_name="api_staff_message_deliveries"
    )
    op.drop_table("api_staff_message_deliveries")
