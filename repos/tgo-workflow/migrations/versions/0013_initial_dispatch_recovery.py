"""Recover employee approval runs committed before initial broker publication.

Revision ID: 0013_initial_dispatch_recovery
Revises: 0012_workflow_source_message
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_initial_dispatch_recovery"
down_revision = "0012_workflow_source_message"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_workflow_executions",
        sa.Column("last_initial_dispatch_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "wf_workflow_executions",
        sa.Column("initial_dispatch_attempts", sa.Integer(),
                  server_default="0", nullable=False),
    )
    op.create_index(
        "ix_wf_initial_dispatch_recovery", "wf_workflow_executions",
        ["status", "execution_mode", "last_initial_dispatch_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    attempted = connection.execute(sa.text(
        "SELECT 1 FROM wf_workflow_executions WHERE "
        "initial_dispatch_attempts <> 0 OR last_initial_dispatch_at IS NOT NULL "
        "LIMIT 1"
    )).scalar_one_or_none()
    if attempted is not None:
        raise RuntimeError("Cannot discard initial workflow dispatch recovery state")
    op.drop_index("ix_wf_initial_dispatch_recovery",
                  table_name="wf_workflow_executions")
    op.drop_column("wf_workflow_executions", "initial_dispatch_attempts")
    op.drop_column("wf_workflow_executions", "last_initial_dispatch_at")
