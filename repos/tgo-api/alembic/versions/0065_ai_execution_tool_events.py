"""Retain payload-free tool events for each AI execution attempt.

Revision ID: 0065_ai_execution_tool_events
Revises: 0064_ai_execution_attempts
"""

from alembic import op
import sqlalchemy as sa

revision = "0065_ai_execution_tool_events"
down_revision = "0064_ai_execution_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_ai_execution_tool_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("tool_call_id", sa.String(128)),
        sa.Column("tool_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_id"],
                                ["api_ai_execution_attempts.execution_id"],
                                ondelete="CASCADE"),
        sa.CheckConstraint("sequence_no > 0", name="ck_ai_tool_event_sequence"),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'failed')",
            name="ck_ai_tool_event_status",
        ),
        sa.UniqueConstraint("execution_id", "sequence_no",
                            name="uq_ai_tool_event_sequence"),
    )
    op.create_index(
        "ix_ai_tool_event_project_execution", "api_ai_execution_tool_events",
        ["project_id", "execution_id", "sequence_no"],
    )


def downgrade() -> None:
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM api_ai_execution_tool_events LIMIT 1")
    ).first() is not None:
        raise RuntimeError("AI tool events exist; archive them before downgrade")
    op.drop_index("ix_ai_tool_event_project_execution",
                  table_name="api_ai_execution_tool_events")
    op.drop_table("api_ai_execution_tool_events")
