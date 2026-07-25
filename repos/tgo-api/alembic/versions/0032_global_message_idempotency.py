"""Add channel-neutral AI and handoff message idempotency.

Revision ID: 0032_global_message_idempotency
Revises: 0031_qwen37_embedding
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0032_global_message_idempotency"
down_revision: Union[str, None] = "0031_qwen37_embedding"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "api_ai_interaction_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("platform_id", sa.UUID(), nullable=False),
        sa.Column("visitor_id", sa.UUID(), nullable=False),
        sa.Column("channel_id", sa.String(length=255), nullable=False),
        sa.Column("channel_type", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "response_client_msg_no",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_ai_interaction_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["platform_id"],
            ["api_platforms.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["api_projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["visitor_id"],
            ["api_visitors.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "platform_id",
            "channel_id",
            "channel_type",
            "source_message_id",
            name="uq_ai_interaction_global_message",
        ),
        sa.UniqueConstraint(
            "response_client_msg_no",
            name="uq_ai_interaction_response_client_msg_no",
        ),
    )
    op.create_index(
        "ix_ai_interaction_project_status_created",
        "api_ai_interaction_runs",
        ["project_id", "status", "created_at"],
    )
    op.create_index(
        "ix_ai_interaction_visitor_created",
        "api_ai_interaction_runs",
        ["visitor_id", "created_at"],
    )

    op.add_column(
        "api_manual_service_requests",
        sa.Column("source_message_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "api_manual_service_requests",
        sa.Column("routing_reason", sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_manual_service_source_message",
        "api_manual_service_requests",
        ["project_id", "visitor_id", "source_message_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_manual_service_source_message",
        "api_manual_service_requests",
        type_="unique",
    )
    op.drop_column("api_manual_service_requests", "routing_reason")
    op.drop_column("api_manual_service_requests", "source_message_id")

    op.drop_index(
        "ix_ai_interaction_visitor_created",
        table_name="api_ai_interaction_runs",
    )
    op.drop_index(
        "ix_ai_interaction_project_status_created",
        table_name="api_ai_interaction_runs",
    )
    op.drop_table("api_ai_interaction_runs")
