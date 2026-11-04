"""Retain verified source message and channel on workflow execution records.

Revision ID: 0012_workflow_source_message
Revises: 0011_aftersales_intake
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_workflow_source_message"
down_revision = "0011_aftersales_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_workflow_executions",
        sa.Column("source_message_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "wf_workflow_executions",
        sa.Column("source_channel", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    connection = op.get_bind()
    traced = connection.execute(sa.text(
        "SELECT 1 FROM wf_workflow_executions WHERE "
        "source_message_id IS NOT NULL OR source_channel IS NOT NULL LIMIT 1"
    )).scalar_one_or_none()
    if traced is not None:
        raise RuntimeError("Cannot discard verified workflow source message origin")
    op.drop_column("wf_workflow_executions", "source_channel")
    op.drop_column("wf_workflow_executions", "source_message_id")
