"""Retain the employee identity accepted for a workflow run.

Revision ID: 0004_employee_execution_binding
Revises: 0003_workflow_releases
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_employee_execution_binding"
down_revision = "0003_workflow_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_workflow_executions",
        sa.Column("employee_agent_id", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    connection = op.get_bind()
    employee_run = connection.execute(
        sa.text(
            "SELECT 1 FROM wf_workflow_executions "
            "WHERE employee_agent_id IS NOT NULL LIMIT 1"
        )
    ).scalar_one_or_none()
    if employee_run is not None:
        raise RuntimeError(
            "Cannot remove employee execution identity while employee runs exist"
        )
    op.drop_column("wf_workflow_executions", "employee_agent_id")
