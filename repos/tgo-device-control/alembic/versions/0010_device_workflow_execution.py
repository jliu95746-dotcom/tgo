"""Link a device session to its trusted parent workflow execution.

Revision ID: 0010_device_workflow_execution
Revises: 0009_device_business_system
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0010_device_workflow_execution"
down_revision = "0009_device_business_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dc_sessions",
        sa.Column("workflow_execution_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_dc_sessions_workflow_execution_id",
        "dc_sessions", ["workflow_execution_id"],
    )


def downgrade() -> None:
    op.execute("LOCK TABLE dc_sessions IN ACCESS EXCLUSIVE MODE")
    has_links = op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM dc_sessions WHERE workflow_execution_id IS NOT NULL)"
    )).scalar()
    if has_links:
        raise RuntimeError("Refusing to discard device workflow execution links")
    op.drop_index("ix_dc_sessions_workflow_execution_id", table_name="dc_sessions")
    op.drop_column("dc_sessions", "workflow_execution_id")
