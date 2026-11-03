"""Persist the exact definition accepted for each workflow execution.

Revision ID: 0002_execution_snapshots
Revises: 0001_init
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_execution_snapshots"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical rows remain nullable: their original definitions cannot be recovered.
    op.add_column("wf_workflow_executions", sa.Column("definition_snapshot", sa.JSON(), nullable=True))
    op.add_column("wf_workflow_executions", sa.Column("workflow_version", sa.Integer(), nullable=True))
    op.add_column("wf_workflow_executions", sa.Column("definition_digest", sa.String(64), nullable=True))
    op.add_column("wf_workflow_executions", sa.Column("execution_mode", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("wf_workflow_executions", "execution_mode")
    op.drop_column("wf_workflow_executions", "definition_digest")
    op.drop_column("wf_workflow_executions", "workflow_version")
    op.drop_column("wf_workflow_executions", "definition_snapshot")
