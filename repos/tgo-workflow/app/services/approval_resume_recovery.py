"""Fence resumed owners and retain action intents before an external call."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import JsonValue
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import WorkflowApprovalRequest
from app.models.execution import NodeExecution, WorkflowExecution
from app.schemas.approval_reconciliation import ReconciliationLedger
from app.services.execution_lifecycle import ExecutionStopped

RECONCILIATION_REQUIRED = (
    "审批后执行进程已中断，已开始的步骤结果待核验；"
    "系统没有自动重复执行，请核对业务执行记录后处理。"
)


async def _owned_run(
    db: AsyncSession,
    project_id: str,
    execution_id: str,
    segment: datetime | None,
    *,
    allow_cancelled: bool = False,
) -> WorkflowExecution:
    if segment is None:
        raise ExecutionStopped("审批恢复执行归属已失效。")
    run = await db.scalar(
        select(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status.in_(
                ("running", "cancelled") if allow_cancelled else ("running",)
            ),
            WorkflowExecution.active_segment_started_at == segment,
            WorkflowExecution.active_approval_id.is_not(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        await db.rollback()
        raise ExecutionStopped("审批恢复执行归属已失效。")
    return run


async def begin_resume_node(
    db: AsyncSession,
    project_id: str,
    execution_id: str,
    node_id: str,
    node_type: str,
    segment: datetime | None,
) -> str:
    run = await _owned_run(db, project_id, execution_id, segment)
    existing = await db.scalar(
        select(NodeExecution.id).where(
            NodeExecution.execution_id == execution_id,
            NodeExecution.project_id == project_id,
            NodeExecution.node_id == node_id,
            NodeExecution.started_at >= run.active_segment_started_at,
        )
    )
    if existing is not None:
        await db.rollback()
        raise ExecutionStopped("本次恢复已开始该步骤，不重复执行。")
    receipt = NodeExecution(
        project_id=project_id,
        execution_id=execution_id,
        node_id=node_id,
        node_type=node_type,
        status="running",
        input=None,
        output=None,
        started_at=datetime.now(UTC),
    )
    db.add(receipt)
    await db.flush()
    identifier = receipt.id
    await db.commit()
    return identifier


async def finish_resume_node(
    db: AsyncSession,
    project_id: str,
    execution_id: str,
    receipt_id: str,
    segment: datetime | None,
    *,
    status: str,
    inputs: dict[str, JsonValue],
    output: JsonValue,
    error: str | None,
    duration: int,
    completed_at: datetime | None = None,
    commit: bool = True,
) -> None:
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError("恢复步骤的完成状态无效。")
    await _owned_run(
        db,
        project_id,
        execution_id,
        segment,
        allow_cancelled=status == "cancelled",
    )
    receipt = await db.scalar(
        select(NodeExecution)
        .where(
            NodeExecution.id == receipt_id,
            NodeExecution.project_id == project_id,
            NodeExecution.execution_id == execution_id,
            NodeExecution.status == "running",
            NodeExecution.started_at >= segment,
        )
        .with_for_update()
    )
    if receipt is None:
        await db.rollback()
        raise ExecutionStopped("恢复步骤已结束或归属已失效。")
    await db.execute(
        update(NodeExecution)
        .where(NodeExecution.id == receipt_id)
        .values(
            status=status,
            input=inputs,
            output=output,
            error=error,
            duration=duration,
            completed_at=completed_at or datetime.now(UTC),
        )
    )
    await db.flush()
    if commit:
        await db.commit()


async def recover_interrupted_approval(
    db: AsyncSession,
    project_id: str,
    execution_id: str,
    *,
    cutoff: datetime,
) -> Literal["requeued", "reconciliation_required"] | None:
    """Requeue only a timed-out claim whose successor action never began."""
    run = await db.scalar(
        select(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "running",
            WorkflowExecution.active_approval_id.is_not(None),
            WorkflowExecution.active_segment_started_at <= cutoff,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        await db.commit()
        return None
    request = await db.get(WorkflowApprovalRequest, run.active_approval_id)
    if (
        request is None
        or request.project_id != project_id
        or request.execution_id != execution_id
        or request.status != "approved"
        or request.decided_at is None
    ):
        await db.commit()
        return None
    started = await db.scalar(
        select(NodeExecution.id)
        .where(
            NodeExecution.execution_id == execution_id,
            NodeExecution.project_id == project_id,
            NodeExecution.started_at >= request.decided_at,
        )
        .limit(1)
    )
    if started is not None:
        # An interrupted call might have changed the external system. Keep the
        # encrypted checkpoint for review and never replay it automatically.
        run.error = RECONCILIATION_REQUIRED
        run.active_segment_started_at = None
        ledger = ReconciliationLedger.model_validate(
            request.reconciliation_ledger or {},
        )
        ledger.generation += 1
        ledger.started_at = datetime.now(UTC)
        request.reconciliation_ledger = ledger.model_dump(mode="json")
        await db.commit()
        return "reconciliation_required"
    run.status = "pending_resume"
    run.active_segment_started_at = None
    run.error = "执行进程已中断，等待从原审批继续。"
    await db.commit()
    return "requeued"
