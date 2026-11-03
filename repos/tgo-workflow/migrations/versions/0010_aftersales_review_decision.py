"""Persist the staff decision on an internal aftersales review.

Revision ID: 0010_aftersales_review_decision
Revises: 0009_aftersales_review_task
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_aftersales_review_decision"
down_revision = "0009_aftersales_review_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_aftersales_reviews",
        sa.Column("review_result", sa.String(40), nullable=True),
    )
    op.add_column(
        "wf_aftersales_reviews",
        sa.Column("reviewed_by", sa.String(36), nullable=True),
    )
    op.add_column(
        "wf_aftersales_reviews",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    connection = op.get_bind()
    decided = connection.execute(sa.text(
        "SELECT 1 FROM wf_aftersales_reviews WHERE status = 'reviewed' LIMIT 1"
    )).scalar_one_or_none()
    if decided is not None:
        raise RuntimeError("Cannot discard recorded staff review decisions")
    op.drop_column("wf_aftersales_reviews", "reviewed_at")
    op.drop_column("wf_aftersales_reviews", "reviewed_by")
    op.drop_column("wf_aftersales_reviews", "review_result")
