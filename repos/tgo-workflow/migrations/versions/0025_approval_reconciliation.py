"""Preserve encrypted progress and staff reconciliation decisions."""

import sqlalchemy as sa
from alembic import op

revision = "0025_approval_reconciliation"
down_revision = "0024_workflow_ai_result_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_approval_requests",
        sa.Column("resume_checkpoint", sa.JSON(), nullable=True),
    )
    op.add_column(
        "wf_approval_requests",
        sa.Column("reconciliation_ledger", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    used = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM wf_approval_requests WHERE "
                "(resume_checkpoint IS NOT NULL "
                "AND CAST(resume_checkpoint AS TEXT) <> 'null') "
                "OR (reconciliation_ledger IS NOT NULL "
                "AND CAST(reconciliation_ledger AS TEXT) <> 'null') LIMIT 1"
            )
        )
        .first()
    )
    if used is not None:
        raise RuntimeError(
            "Preserve approval progress and review history before downgrade"
        )
    op.drop_column("wf_approval_requests", "reconciliation_ledger")
    op.drop_column("wf_approval_requests", "resume_checkpoint")
