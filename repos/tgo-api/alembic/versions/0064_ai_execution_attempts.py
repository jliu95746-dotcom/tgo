"""Retain the AI model execution ID for each customer-message attempt.

Revision ID: 0064_ai_execution_attempts
Revises: 0063_shadow_gate_signoffs
"""

from alembic import op
import sqlalchemy as sa

revision = "0064_ai_execution_attempts"
down_revision = "0063_shadow_gate_signoffs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_ai_execution_attempts",
        sa.Column("execution_id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("interaction_run_id", sa.Uuid(), nullable=False),
        sa.Column("response_client_msg_no", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interaction_run_id"], ["api_ai_interaction_runs.id"],
                                ondelete="CASCADE"),
        sa.UniqueConstraint("interaction_run_id", "response_client_msg_no",
                            name="uq_ai_execution_attempt_reply"),
    )
    op.create_index(
        "ix_ai_execution_attempt_project_interaction", "api_ai_execution_attempts",
        ["project_id", "interaction_run_id", "started_at"],
    )


def downgrade() -> None:
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM api_ai_execution_attempts LIMIT 1")
    ).first() is not None:
        raise RuntimeError("AI execution attempts exist; archive them before downgrade")
    op.drop_index("ix_ai_execution_attempt_project_interaction",
                  table_name="api_ai_execution_attempts")
    op.drop_table("api_ai_execution_attempts")
