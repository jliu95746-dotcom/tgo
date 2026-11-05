"""Keep encrypted, short-lived answers for the exact original AI call."""
from alembic import op
import sqlalchemy as sa

revision = "0024_workflow_ai_result_cache"
down_revision = "0023_workflow_ai_usage_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_workflow_executions",
        sa.Column(
            "ai_result_receipts",
            sa.JSON(),
            nullable=True,
        ),
    )
    op.add_column(
        "wf_workflow_executions",
        sa.Column(
            "ai_result_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_wf_workflow_executions_ai_result_expires_at",
        "wf_workflow_executions",
        ["ai_result_expires_at"],
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM wf_workflow_executions "
                "WHERE ai_result_receipts IS NOT NULL "
                "AND CAST(ai_result_receipts AS TEXT) <> 'null' LIMIT 1"
            )
        )
        .first()
        is not None
    ):
        raise RuntimeError(
            "Workflow AI call receipts exist; preserve them before downgrade"
        )
    op.drop_index(
        "ix_wf_workflow_executions_ai_result_expires_at",
        table_name="wf_workflow_executions",
    )
    op.drop_column("wf_workflow_executions", "ai_result_expires_at")
    op.drop_column("wf_workflow_executions", "ai_result_receipts")
