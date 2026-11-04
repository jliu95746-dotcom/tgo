"""Persist one bounded internal aftersales intake on its reviewed task.

Revision ID: 0011_aftersales_intake
Revises: 0010_aftersales_review_decision
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_aftersales_intake"
down_revision = "0010_aftersales_review_decision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("intake_order_reference", sa.String(100), nullable=True),
        sa.Column("intake_sku", sa.String(100), nullable=True),
        sa.Column("intake_request_reason", sa.String(500), nullable=True),
        sa.Column("intake_policy_reference", sa.String(200), nullable=True),
        sa.Column("intake_evidence_file_ids", sa.JSON(), nullable=True),
        sa.Column("intake_digest", sa.String(64), nullable=True),
        sa.Column("intake_prepared_by", sa.String(36), nullable=True),
        sa.Column("intake_prepared_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("wf_aftersales_reviews", column)


def downgrade() -> None:
    connection = op.get_bind()
    prepared = connection.execute(sa.text(
        "SELECT 1 FROM wf_aftersales_reviews WHERE intake_digest IS NOT NULL LIMIT 1"
    )).scalar_one_or_none()
    if prepared is not None:
        raise RuntimeError("Cannot discard prepared internal aftersales receipts")
    for name in (
        "intake_prepared_at", "intake_prepared_by", "intake_digest",
        "intake_evidence_file_ids", "intake_policy_reference",
        "intake_request_reason", "intake_sku", "intake_order_reference",
    ):
        op.drop_column("wf_aftersales_reviews", name)
