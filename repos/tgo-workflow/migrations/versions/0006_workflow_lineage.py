"""Store bounded workflow call chains and parent execution identity.

Revision ID: 0006_workflow_lineage
Revises: 0005_execution_origin
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_workflow_lineage"
down_revision = "0005_execution_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wf_workflow_executions", sa.Column("parent_execution_id", sa.String(36), nullable=True))
    op.add_column("wf_workflow_executions", sa.Column("workflow_lineage", sa.JSON(), nullable=True))
    op.create_index(
        "ix_wf_execution_parent", "wf_workflow_executions",
        ["project_id", "parent_execution_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    traced = connection.execute(sa.text(
        "SELECT 1 FROM wf_workflow_executions WHERE parent_execution_id IS NOT NULL "
        "OR workflow_lineage IS NOT NULL LIMIT 1"
    )).scalar_one_or_none()
    if traced is not None:
        raise RuntimeError("Cannot remove workflow lineage while nested runs exist")
    op.drop_index("ix_wf_execution_parent", table_name="wf_workflow_executions")
    op.drop_column("wf_workflow_executions", "workflow_lineage")
    op.drop_column("wf_workflow_executions", "parent_execution_id")
