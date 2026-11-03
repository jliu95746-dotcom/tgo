"""Persist approval decisions and their exact workflow continuation cursor.

Revision ID: 0007_workflow_approval_state
Revises: 0006_workflow_lineage
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_workflow_approval_state"
down_revision = "0006_workflow_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_workflow_executions",
        sa.Column("active_approval_id", sa.String(36), nullable=True),
    )
    op.create_table(
        "wf_approval_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("approver_staff_id", sa.String(36), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["wf_workflow_executions.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint("execution_id", "node_id", name="uq_wf_approval_execution_node"),
    )
    op.create_index(
        "ix_wf_approval_assignee_status", "wf_approval_requests",
        ["project_id", "approver_staff_id", "status"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM wf_approval_requests LIMIT 1"
    )).scalar_one_or_none() is not None:
        raise RuntimeError("Cannot remove non-empty workflow approval state")
    if connection.execute(sa.text(
        "SELECT 1 FROM wf_workflow_executions "
        "WHERE active_approval_id IS NOT NULL LIMIT 1"
    )).scalar_one_or_none() is not None:
        raise RuntimeError("Cannot remove active approval references")
    op.drop_index("ix_wf_approval_assignee_status", table_name="wf_approval_requests")
    op.drop_table("wf_approval_requests")
    op.drop_column("wf_workflow_executions", "active_approval_id")
