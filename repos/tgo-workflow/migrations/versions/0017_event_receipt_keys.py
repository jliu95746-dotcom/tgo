"""Keep the proof key identity after event input retention clears the body.

Revision ID: 0017_event_receipt_keys
Revises: 0016_terminal_approval_checkpoints
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_event_receipt_keys"
down_revision = "0016_terminal_approval_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_workflow_executions",
        sa.Column("event_receipt_key_id", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE wf_workflow_executions IN ACCESS EXCLUSIVE MODE"
            )
        )
    if connection.scalar(
        sa.text(
            "SELECT count(*) FROM wf_workflow_executions "
            "WHERE event_receipt_key_id IS NOT NULL"
        )
    ):
        raise RuntimeError("已有版本化事件回执，不能删除密钥编号；请保留兼容代码回退")
    op.drop_column("wf_workflow_executions", "event_receipt_key_id")
