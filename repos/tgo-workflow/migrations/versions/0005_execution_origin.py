"""Persist server supplied AI and customer request lineage on workflow runs.

Revision ID: 0005_execution_origin
Revises: 0004_employee_execution_binding
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_execution_origin"
down_revision = "0004_employee_execution_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_workflow_executions",
        sa.Column("ai_request_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "wf_workflow_executions",
        sa.Column("business_request_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "wf_workflow_executions",
        sa.Column("visitor_session_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_wf_execution_business_request",
        "wf_workflow_executions",
        ["project_id", "business_request_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    origin_exists = connection.execute(sa.text(
        "SELECT 1 FROM wf_workflow_executions WHERE "
        "ai_request_id IS NOT NULL OR business_request_id IS NOT NULL "
        "OR visitor_session_id IS NOT NULL LIMIT 1"
    )).scalar_one_or_none()
    if origin_exists is not None:
        raise RuntimeError("Cannot remove workflow origin while traced runs exist")
    op.drop_index("ix_wf_execution_business_request", table_name="wf_workflow_executions")
    op.drop_column("wf_workflow_executions", "visitor_session_id")
    op.drop_column("wf_workflow_executions", "business_request_id")
    op.drop_column("wf_workflow_executions", "ai_request_id")
