"""Bound redelivery of approved workflow continuations.

Revision ID: 0008_resume_dispatch_lease
Revises: 0007_workflow_approval_state
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_resume_dispatch_lease"
down_revision = "0007_workflow_approval_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_approval_requests",
        sa.Column("last_resume_dispatch_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "wf_approval_requests",
        sa.Column("resume_dispatch_attempts", sa.Integer(),
                  server_default="0", nullable=False),
    )
    op.create_index(
        "ix_wf_approval_resume_dispatch", "wf_approval_requests",
        ["project_id", "status", "last_resume_dispatch_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    pending = connection.execute(sa.text(
        "SELECT 1 FROM wf_approval_requests a "
        "JOIN wf_workflow_executions e ON e.id = a.execution_id "
        "WHERE a.status = 'approved' AND e.status = 'pending_resume' LIMIT 1"
    )).scalar_one_or_none()
    if pending is not None:
        raise RuntimeError("Cannot remove dispatch lease while approved runs await resume")
    op.drop_index("ix_wf_approval_resume_dispatch", table_name="wf_approval_requests")
    op.drop_column("wf_approval_requests", "resume_dispatch_attempts")
    op.drop_column("wf_approval_requests", "last_resume_dispatch_at")
