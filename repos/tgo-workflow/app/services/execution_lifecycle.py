"""Atomic state changes keep duplicate and late tasks from reviving a run."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import JsonValue
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import WorkflowExecution

DISPATCH_FAILURE = "工作流任务未能提交，请稍后重试。"


class ExecutionStopped(RuntimeError):
    """The persisted run is no longer eligible to execute another node."""


async def claim_execution(
    db: AsyncSession, execution_id: str, workflow_id: str, project_id: str,
) -> bool:
    result = await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.workflow_id == workflow_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "pending",
        )
        .values(status="running", started_at=datetime.now(timezone.utc))
        .returning(WorkflowExecution.id)
    )
    claimed = result.scalar_one_or_none() is not None
    await db.commit()
    return claimed


async def fail_pending_dispatch(
    db: AsyncSession, execution_id: str, project_id: str,
) -> bool:
    result = await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "pending",
        )
        .values(
            status="failed", error=DISPATCH_FAILURE,
            completed_at=datetime.now(timezone.utc), duration=0,
        )
        .returning(WorkflowExecution.id)
    )
    changed = result.scalar_one_or_none() is not None
    await db.commit()
    return changed


async def require_running(
    db: AsyncSession, execution_id: str, project_id: str,
) -> None:
    if await read_execution_status(db, execution_id, project_id) != "running":
        raise ExecutionStopped("工作流已停止，不再执行后续节点。")


async def read_execution_status(
    db: AsyncSession, execution_id: str, project_id: str,
) -> str | None:
    result = await db.execute(
        select(WorkflowExecution.status).where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
        )
    )
    status = result.scalar_one_or_none()
    # Do not hold a read transaction while a node calls an external service.
    await db.commit()
    return status


async def cancel_running_execution(
    db: AsyncSession, execution_id: str, project_id: str,
) -> None:
    await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "running",
        )
        .values(
            status="cancelled", completed_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()


async def finish_running_execution(
    db: AsyncSession, execution_id: str, project_id: str,
    status: Literal["completed", "failed"], output: JsonValue,
    error: str | None, duration: int,
) -> bool:
    result = await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "running",
        )
        .values(
            status=status, output=output, error=error, duration=duration,
            completed_at=datetime.now(timezone.utc),
        )
        .returning(WorkflowExecution.id)
    )
    changed = result.scalar_one_or_none() is not None
    await db.commit()
    return changed
