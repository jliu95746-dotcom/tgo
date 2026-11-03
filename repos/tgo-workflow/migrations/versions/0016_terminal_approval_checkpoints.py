"""Remove private continuation context from historical terminal approvals.

Revision ID: 0016_terminal_approval_checkpoints
Revises: 0015_terminal_input_privacy

The removed context cannot be restored by downgrade. Verify a backup and stop
workflow workers before applying this migration to existing approval records.
"""

import hashlib
import json
import os

import sqlalchemy as sa
from alembic import op

revision = "0016_terminal_approval_checkpoints"
down_revision = "0015_terminal_input_privacy"
branch_labels = None
depends_on = None

BATCH_SIZE = 250
TERMINAL_STATUSES = (
    "completed",
    "failed",
    "cancelled",
    "rejected",
    "expired",
)


def _preview_digest(approval_id: str, execution_id: str, summary: str) -> str:
    payload = json.dumps(
        ["workflow-approval-preview-v1", approval_id, execution_id, summary],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def upgrade() -> None:
    connection = op.get_bind()
    runs = sa.table(
        "wf_workflow_executions",
        sa.column("id", sa.String),
        sa.column("status", sa.String),
    )
    approvals = sa.table(
        "wf_approval_requests",
        sa.column("id", sa.String),
        sa.column("execution_id", sa.String),
        sa.column("summary", sa.String),
        sa.column("checkpoint", sa.JSON(none_as_null=True)),
    )
    terminal_approval = (
        sa.select(approvals.c.id)
        .join(runs, runs.c.id == approvals.c.execution_id)
        .where(runs.c.status.in_(TERMINAL_STATUSES))
    )
    if (
        connection.execute(terminal_approval.limit(1)).first() is not None
        and os.getenv("TGO_WORKFLOW_INPUT_REDACTION_BACKUP_VERIFIED") != "1"
    ):
        raise RuntimeError(
            "Existing terminal workflow approvals require a verified backup "
            "and TGO_WORKFLOW_INPUT_REDACTION_BACKUP_VERIFIED=1"
        )
    # This revision ID exceeds Alembic's default VARCHAR(32). Widen before
    # Alembic records it, preserving all historical version values.
    if connection.dialect.name == "postgresql" and sa.inspect(
        connection
    ).has_table("alembic_version"):
        connection.execute(
            sa.text(
                "ALTER TABLE alembic_version "
                "ALTER COLUMN version_num TYPE VARCHAR(128)"
            )
        )
    update_approval = (
        sa.update(approvals)
        .where(approvals.c.id == sa.bindparam("row_id"))
        .values(checkpoint=sa.bindparam("redacted_checkpoint"))
    )
    last_id = ""
    while True:
        rows = (
            connection.execute(
                sa.select(
                    approvals.c.id,
                    approvals.c.execution_id,
                    approvals.c.summary,
                    approvals.c.checkpoint,
                )
                .join(runs, runs.c.id == approvals.c.execution_id)
                .where(
                    runs.c.status.in_(TERMINAL_STATUSES),
                    approvals.c.id > last_id,
                )
                .order_by(approvals.c.id)
                .limit(BATCH_SIZE)
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        changes = []
        for row in rows:
            receipt = {
                "capture_status": "redacted",
                "approval_preview_digest": _preview_digest(
                    row["id"],
                    row["execution_id"],
                    row["summary"],
                ),
            }
            if row["checkpoint"] != receipt:
                changes.append(
                    {
                        "row_id": row["id"],
                        "redacted_checkpoint": receipt,
                    }
                )
        if changes:
            connection.execute(update_approval, changes)
        last_id = rows[-1]["id"]


def downgrade() -> None:
    raise RuntimeError(
        "Terminal approval checkpoint migration cannot be reversed"
    )
