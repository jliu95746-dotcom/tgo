"""Create idempotent internal aftersales review tasks.

Revision ID: 0009_aftersales_review_task
Revises: 0008_resume_dispatch_lease
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_aftersales_review_task"
down_revision = "0008_resume_dispatch_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wf_aftersales_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), sa.ForeignKey(
            "wf_workflow_executions.id", ondelete="RESTRICT",
        ), nullable=False),
        sa.Column("approval_id", sa.String(), sa.ForeignKey(
            "wf_approval_requests.id", ondelete="RESTRICT",
        ), nullable=False),
        sa.Column("visitor_session_id", sa.String(36), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("approval_id", name="uq_wf_aftersales_review_approval"),
    )
    op.create_index(
        "ix_wf_aftersales_review_project_status", "wf_aftersales_reviews",
        ["project_id", "status"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_reviews = connection.execute(
        sa.text("SELECT 1 FROM wf_aftersales_reviews LIMIT 1")
    ).scalar_one_or_none()
    if has_reviews:
        raise RuntimeError("Cannot drop persisted aftersales review tasks")
    op.drop_index(
        "ix_wf_aftersales_review_project_status",
        table_name="wf_aftersales_reviews",
    )
    op.drop_table("wf_aftersales_reviews")
