"""Capture accepted execution data and refuse missing or modified definitions."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import WorkflowExecution
from app.services.employee_workflow_binding import EmployeeWorkflowBinding, saved_employee_binding

_json_object = TypeAdapter(dict[str, JsonValue])
SNAPSHOT_ERROR = "执行快照缺失或校验失败，请重新提交测试。"


def copy_json_object(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    try:
        owned = deepcopy(_json_object.validate_python(value))
        # JSON storage must also reject non-finite numbers before committing a run.
        json.dumps(owned, allow_nan=False)
        return owned
    except (ValueError, TypeError):
        raise ValueError("工作流定义或输入不是有效的 JSON 数据。") from None


def definition_digest(definition: dict[str, JsonValue]) -> str:
    encoded = json.dumps(
        definition, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def input_receipt_hmac(
    inputs: dict[str, JsonValue], secret: str, execution_id: str,
) -> str:
    """Compare a retried invocation after its terminal input has been removed."""
    if not secret or not execution_id:
        raise ValueError("工作流输入核验密钥未配置。")
    encoded = json.dumps(
        inputs, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hmac.new(
        secret.encode("utf-8"),
        b"tgo-workflow-invocation-input:v1\x00"
        + execution_id.encode("utf-8") + b"\x00" + encoded,
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class ExecutionSnapshot:
    definition: dict[str, JsonValue]
    inputs: dict[str, JsonValue]
    workflow_version: int
    employee_binding: EmployeeWorkflowBinding | None = None
    workflow_lineage: tuple[str, ...] = ()


async def load_execution_snapshot(
    db: AsyncSession, execution_id: str, workflow_id: str, project_id: str,
) -> ExecutionSnapshot:
    result = await db.execute(select(WorkflowExecution).where(
        WorkflowExecution.id == execution_id,
        WorkflowExecution.workflow_id == workflow_id,
        WorkflowExecution.project_id == project_id,
    ))
    row = result.scalar_one_or_none()
    if row is None or row.definition_snapshot is None or row.definition_digest is None:
        raise RuntimeError(SNAPSHOT_ERROR)
    if row.workflow_version is None or row.workflow_version < 1:
        raise RuntimeError(SNAPSHOT_ERROR)
    try:
        definition = copy_json_object(row.definition_snapshot)
        inputs = copy_json_object(row.input if row.input is not None else {})
        if not hmac.compare_digest(definition_digest(definition), row.definition_digest):
            raise ValueError("Digest mismatch")
    except (TypeError, ValueError):
        raise RuntimeError(SNAPSHOT_ERROR) from None
    version = row.workflow_version
    employee_binding = saved_employee_binding(
        project_id, row.employee_agent_id, workflow_id, row.execution_mode,
        version, row.release_id, row.definition_digest,
    )
    await db.commit()
    return ExecutionSnapshot(
        definition, inputs, version, employee_binding,
        tuple(row.workflow_lineage or (workflow_id,)),
    )
