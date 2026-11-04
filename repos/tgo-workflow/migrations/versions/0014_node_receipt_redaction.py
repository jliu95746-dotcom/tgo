"""Remove legacy private node receipt payloads.

Revision ID: 0014_node_receipt_redaction
Revises: 0013_initial_dispatch_recovery

The removed values cannot be reconstructed by downgrade. Back up and verify
restore before applying this migration, with workflow workers stopped. Existing
rows require TGO_NODE_RECEIPT_REDACTION_BACKUP_VERIFIED=1 for the upgrade.
"""

import os
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "0014_node_receipt_redaction"
down_revision = "0013_initial_dispatch_recovery"
branch_labels = None
depends_on = None

BATCH_SIZE = 250
NODE_ERROR = "工作流节点执行失败。"


def _input_evidence(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return {"capture_status": "redacted"}
    if value.get("capture_status") == "redacted":
        evidence: dict[str, object] = {"capture_status": "redacted"}
        field_count = value.get("field_count")
        if type(field_count) is int and 0 <= field_count <= 10000:
            evidence["field_count"] = field_count
        return evidence
    return {"capture_status": "redacted", "field_count": len(value)}


def _output_evidence(node_type: str, value: object) -> object:
    if value is None or node_type == "answer":
        return value
    if not isinstance(value, dict):
        return {"capture_status": "redacted"}
    if node_type == "business_query":
        query_id = value.get("query_id")
        connection_id = value.get("connection_id")
        operation = value.get("operation")
        if (
            isinstance(query_id, str)
            and isinstance(connection_id, str)
            and isinstance(operation, str)
            and operation in {"order_query", "logistics_query"}
        ):
            try:
                return {
                    "query_id": str(UUID(query_id)),
                    "connection_id": str(UUID(connection_id)),
                    "operation": operation,
                }
            except ValueError:
                pass
        return _input_evidence(value)
    if node_type == "condition" and type(value.get("result")) is bool:
        return {"result": value["result"]}
    if node_type == "approval" and value.get("requested") is True:
        return {"requested": True}
    evidence = _input_evidence(value)
    assert evidence is not None  # A dict input always produces a receipt.
    agent_execution_id = value.get("agent_execution_id")
    if node_type == "agent" and isinstance(agent_execution_id, str):
        try:
            evidence["agent_execution_id"] = str(UUID(agent_execution_id))
        except ValueError:
            pass
    status_code = value.get("status_code")
    if (
        node_type == "api"
        and type(status_code) is int
        and 100 <= status_code <= 599
    ):
        evidence["status_code"] = status_code
    return evidence


def upgrade() -> None:
    connection = op.get_bind()
    nodes = sa.table(
        "wf_node_executions",
        sa.column("id", sa.String),
        sa.column("node_type", sa.String),
        sa.column("input", sa.JSON(none_as_null=True)),
        sa.column("output", sa.JSON(none_as_null=True)),
        sa.column("error", sa.Text),
    )
    if (
        connection.execute(sa.select(nodes.c.id).limit(1)).first() is not None
        and os.getenv("TGO_NODE_RECEIPT_REDACTION_BACKUP_VERIFIED") != "1"
    ):
        raise RuntimeError(
            "Existing workflow node receipts require a verified backup and "
            "TGO_NODE_RECEIPT_REDACTION_BACKUP_VERIFIED=1 before migration"
        )
    update_nodes = (
        sa.update(nodes)
        .where(nodes.c.id == sa.bindparam("row_id"))
        .values(
            input=sa.bindparam("redacted_input"),
            output=sa.bindparam("redacted_output"),
            error=sa.bindparam("redacted_error"),
        )
    )
    last_id = ""
    while True:
        rows = (
            connection.execute(
                sa.select(
                    nodes.c.id,
                    nodes.c.node_type,
                    nodes.c.input,
                    nodes.c.output,
                    nodes.c.error,
                )
                .where(nodes.c.id > last_id)
                .order_by(nodes.c.id)
                .limit(BATCH_SIZE)
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        changes = []
        for row in rows:
            redacted_input = _input_evidence(row["input"])
            redacted_output = _output_evidence(row["node_type"], row["output"])
            redacted_error = NODE_ERROR if row["error"] is not None else None
            if (
                redacted_input != row["input"]
                or redacted_output != row["output"]
                or redacted_error != row["error"]
            ):
                changes.append(
                    {
                        "row_id": row["id"],
                        "redacted_input": redacted_input,
                        "redacted_output": redacted_output,
                        "redacted_error": redacted_error,
                    }
                )
        if changes:
            connection.execute(update_nodes, changes)
        last_id = rows[-1]["id"]


def downgrade() -> None:
    # Scrubbed private payloads cannot be recovered without a verified backup.
    pass
