"""Protect invocation receipts and release inputs from terminal runs.

Revision ID: 0015_terminal_input_privacy
Revises: 0014_node_receipt_redaction

Existing terminal inputs require a verified backup and stopped workflow
workers. Cleared inputs cannot be restored by a database downgrade.
"""

import hashlib
import hmac
import json
import os

import sqlalchemy as sa
from alembic import op

from app.config import settings

revision = "0015_terminal_input_privacy"
down_revision = "0014_node_receipt_redaction"
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


def _input_hmac(value: dict[str, object], secret: str, run_id: str) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hmac.new(
        secret.encode("utf-8"),
        b"tgo-workflow-invocation-input:v1\x00"
        + run_id.encode("utf-8")
        + b"\x00"
        + encoded,
        hashlib.sha256,
    ).hexdigest()


def upgrade() -> None:
    op.add_column(
        "wf_workflow_executions",
        sa.Column("invocation_input_hmac", sa.String(64), nullable=True),
    )
    connection = op.get_bind()
    runs = sa.table(
        "wf_workflow_executions",
        sa.column("id", sa.String),
        sa.column("status", sa.String),
        sa.column("employee_agent_id", sa.String),
        sa.column("input", sa.JSON(none_as_null=True)),
        sa.column("invocation_input_hmac", sa.String(64)),
    )
    has_terminal_rows = (
        connection.execute(
            sa.select(runs.c.id)
            .where(runs.c.status.in_(TERMINAL_STATUSES))
            .limit(1)
        ).first()
        is not None
    )
    if (
        has_terminal_rows
        and os.getenv("TGO_WORKFLOW_INPUT_REDACTION_BACKUP_VERIFIED") != "1"
    ):
        raise RuntimeError(
            "Existing terminal workflow inputs require a verified backup and "
            "TGO_WORKFLOW_INPUT_REDACTION_BACKUP_VERIFIED=1 before migration"
        )
    key = settings.SECRET_KEY
    secret = key.get_secret_value() if key is not None else ""
    needs_employee_receipt = (
        connection.execute(
            sa.select(runs.c.id)
            .where(
                runs.c.employee_agent_id.is_not(None),
                runs.c.input.is_not(None),
            )
            .limit(1)
        ).first()
        is not None
    )
    if needs_employee_receipt and not secret:
        raise RuntimeError("SECRET_KEY is required to protect workflow inputs")

    terminal_update = (
        sa.update(runs)
        .where(runs.c.id == sa.bindparam("row_id"))
        .values(
            input=None,
            invocation_input_hmac=sa.bindparam("input_hmac"),
        )
    )
    active_update = (
        sa.update(runs)
        .where(runs.c.id == sa.bindparam("row_id"))
        .values(invocation_input_hmac=sa.bindparam("input_hmac"))
    )
    last_id = ""
    while True:
        rows = (
            connection.execute(
                sa.select(
                    runs.c.id,
                    runs.c.status,
                    runs.c.employee_agent_id,
                    runs.c.input,
                )
                .where(runs.c.id > last_id)
                .order_by(runs.c.id)
                .limit(BATCH_SIZE)
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        terminal_changes = []
        active_changes = []
        for row in rows:
            value = row["input"]
            employee_id = row["employee_agent_id"]
            if employee_id is not None and value is not None:
                if not isinstance(value, dict):
                    raise RuntimeError(
                        "Employee workflow input is not a JSON object; "
                        "migration stopped without discarding it"
                    )
                receipt = _input_hmac(value, secret, row["id"])
            else:
                receipt = None
            if row["status"] in TERMINAL_STATUSES:
                if value is not None or receipt is not None:
                    terminal_changes.append(
                        {
                            "row_id": row["id"],
                            "input_hmac": receipt,
                        }
                    )
            elif receipt is not None:
                active_changes.append(
                    {
                        "row_id": row["id"],
                        "input_hmac": receipt,
                    }
                )
        if terminal_changes:
            connection.execute(terminal_update, terminal_changes)
        if active_changes:
            connection.execute(active_update, active_changes)
        last_id = rows[-1]["id"]


def downgrade() -> None:
    raise RuntimeError("Terminal input privacy migration cannot be reversed")
