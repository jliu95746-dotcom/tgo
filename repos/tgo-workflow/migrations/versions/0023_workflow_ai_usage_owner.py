"""Persist private workflow AI quota ownership across worker dispatches.

Revision ID: 0023_workflow_ai_usage_owner
Revises: 0022_execution_segments
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_workflow_ai_usage_owner"
down_revision = "0022_execution_segments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_workflow_executions",
        sa.Column("ai_usage_owner", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM wf_workflow_executions "
                "WHERE ai_usage_owner IS NOT NULL "
                "AND CAST(ai_usage_owner AS TEXT) <> 'null' LIMIT 1"
            )
        )
        .first()
        is not None
    ):
        raise RuntimeError(
            "Private workflow AI ownership exists; preserve records before downgrade"
        )
    op.drop_column("wf_workflow_executions", "ai_usage_owner")
