"""Read the accepted workflow snapshot and private billing principal."""
import hmac
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.execution import WorkflowExecution
from app.schemas.ai_usage import WorkflowAIContext, WorkflowUsageOwner
from app.services.employee_workflow_binding import (
    check_employee_binding,
    saved_employee_binding,
)
from app.services.execution_snapshot import copy_json_object, definition_digest


def request_owner(
    key: str | None,
    staff: str | None,
    version: str | None,
    reservation: str | None,
    lease: str | None,
) -> WorkflowUsageOwner | None:
    # Direct Python endpoint tests use Header defaults rather than wire strings.
    values = [
        value if isinstance(value, str) else None
        for value in (staff, version, reservation, lease)
    ]
    if not any(value is not None for value in values):
        return None
    secret = settings.SECRET_KEY
    if (
        secret is None
        or not isinstance(key, str)
        or not key
        or not hmac.compare_digest(key, secret.get_secret_value())
    ):
        raise HTTPException(403, "工作流额度来源只允许内部服务指定")
    try:
        return WorkflowUsageOwner.model_validate(
            {
                "staff_id": values[0],
                "staff_token_version": values[1],
                "reservation_id": values[2],
                "lease_id": values[3],
            }
        )
    except ValidationError:
        raise HTTPException(403, "工作流额度来源不完整") from None


async def read_context(
    db: AsyncSession,
    project_id: UUID,
    execution_id: UUID,
    node_id: str,
) -> WorkflowAIContext:
    statement = (
        select(WorkflowExecution)
        .where(
            WorkflowExecution.id == str(execution_id),
            WorkflowExecution.project_id == str(project_id),
        )
        .execution_options(populate_existing=True)
    )
    row = await db.scalar(statement)
    if (
        row is None
        or row.project_id != str(project_id)
        or row.status != "running"
    ):
        raise HTTPException(403, "工作流运行或额度来源已失效")
    try:
        if row.definition_snapshot is None:
            raise ValueError("Missing snapshot")
        definition = copy_json_object(row.definition_snapshot)
        if definition_digest(definition) != row.definition_digest:
            raise ValueError("Snapshot mismatch")
        nodes = definition.get("nodes", [])
        if not isinstance(nodes, list):
            raise ValueError("Missing nodes")
        matching = [
            node
            for node in nodes
            if isinstance(node, dict)
            and node.get("id") == node_id
            and node.get("type") == "agent"
        ]
        if len(matching) != 1:
            raise ValueError("Missing agent node")
        data = matching[0].get("data")
        if not isinstance(data, dict):
            raise ValueError("Missing agent configuration")
        context = WorkflowAIContext.model_validate(
            {
                "project_id": project_id,
                "execution_id": execution_id,
                "node_id": node_id,
                "agent_id": data.get("agent_id"),
                "owner": row.ai_usage_owner or {"unmetered": True},
                "status": row.status,
                "workflow_lineage": tuple(
                    row.workflow_lineage or (row.workflow_id,)
                ),
                "expected_device_id": data.get("expected_device_id"),
                "expected_device_policy_digest": data.get(
                    "expected_device_policy_digest"
                ),
            }
        )
        binding = saved_employee_binding(
            row.project_id,
            row.employee_agent_id,
            row.workflow_id,
            row.execution_mode,
            row.workflow_version,
            row.release_id,
            row.definition_digest,
        )
        await check_employee_binding(binding, db)
        current = await db.scalar(statement)
        if current is None or current.status != "running":
            raise ValueError("Execution stopped")
        return context
    except (ValueError, TypeError, PermissionError, RuntimeError):
        raise HTTPException(403, "工作流节点快照或当前授权无法确认") from None
