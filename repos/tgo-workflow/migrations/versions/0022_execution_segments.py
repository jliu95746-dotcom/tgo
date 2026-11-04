"""Track each worker segment without counting human approval waiting time.

Revision ID: 0022_execution_segments
Revises: 0021_webhook_deliveries
"""

from alembic import op
import sqlalchemy as sa

revision = "0022_execution_segments"
down_revision = "0021_webhook_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Do not invent claim timestamps for legacy or waiting executions.
    op.add_column(
        "wf_workflow_executions",
        sa.Column(
            "active_segment_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    recorded = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM wf_workflow_executions "
                "WHERE active_segment_started_at IS NOT NULL LIMIT 1"
            )
        )
        .scalar_one_or_none()
    )
    if recorded is not None:
        raise RuntimeError("Cannot discard workflow execution segment history")
    op.drop_column("wf_workflow_executions", "active_segment_started_at")
