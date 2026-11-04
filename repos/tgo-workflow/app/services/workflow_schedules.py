"""Serialize schedule edits with their immutable command reply transaction."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_schedule import (
    WorkflowSchedule,
    WorkflowScheduleCommand,
)
from app.schemas.node import TimerNodeData
from app.schemas.workflow_schedule_views import ScheduleReleaseContext
from app.schemas.workflow_schedule import (
    ScheduleDisable,
    ScheduleReceipt,
    ScheduleSave,
    ScheduleState,
    ScheduleSummary,
)
from app.services.execution_snapshot import copy_json_object, definition_digest
from app.services.node_readiness import workflow_capabilities
from app.services.schedule_rules import parse_schedule
from app.services.validation_service import ValidationService
from app.services.workflow_releases import (
    production_definition,
    verify_execution_binding,
)
from app.services.timer_runtime_health import get_timer_runtime_state

CONFLICT = "定时计划已有变化或请求编号已被使用，请读取原结果后重试。"


async def _published_timer_context(
    db: AsyncSession, workflow: Workflow,
) -> ScheduleReleaseContext | None:
    # Imported here to preserve the timer binding service dependency direction.
    from app.services.workflow_schedule_views import published_timer_context

    return await published_timer_context(db, workflow)


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _summary(row: WorkflowSchedule) -> ScheduleSummary:
    result = ScheduleSummary.model_validate(row)
    return result.model_copy(
        update={
            "created_at": _utc(result.created_at),
            "updated_at": _utc(result.updated_at),
            "next_run_at": _utc(result.next_run_at)
            if result.next_run_at
            else None,
        }
    )


async def _workflow(
    db: AsyncSession, workflow_id: str, project_id: UUID, *, lock: bool = False
) -> Workflow:
    query = select(Workflow).where(
        Workflow.id == workflow_id, Workflow.project_id == str(project_id)
    )
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "工作流不存在。")
    return row


async def _schedule(
    db: AsyncSession, workflow_id: str, project_id: UUID
) -> WorkflowSchedule | None:
    return await db.scalar(
        select(WorkflowSchedule)
        .where(
            WorkflowSchedule.workflow_id == workflow_id,
            WorkflowSchedule.project_id == str(project_id),
        )
        .execution_options(populate_existing=True)
    )


async def _command(
    db: AsyncSession, project_id: UUID, request_id: UUID
) -> WorkflowScheduleCommand | None:
    return await db.get(
        WorkflowScheduleCommand,
        (str(project_id), str(request_id)),
        populate_existing=True,
    )


def _receipt(
    row: WorkflowScheduleCommand, *, replayed: bool
) -> ScheduleReceipt:
    if row.operation not in {"save", "disable"}:
        raise HTTPException(503, "定时计划原请求回执无法核验。")
    operation: Literal["save", "disable"] = (
        "save" if row.operation == "save" else "disable"
    )
    return ScheduleReceipt(
        request_id=UUID(row.request_id),
        operation=operation,
        schedule=ScheduleSummary.model_validate(row.result_snapshot),
        replayed=replayed,
    )


def _digest(
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    operation: str,
    request: ScheduleSave | ScheduleDisable,
) -> str:
    canonical = json.dumps(
        {
            "contract": "workflow.schedule.command.v1",
            "workflow_id": workflow_id,
            "project_id": str(project_id),
            "actor_id": str(actor_id),
            "operation": operation,
            "request": request.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _replay(
    row: WorkflowScheduleCommand, workflow_id: str, actor_id: UUID, digest: str
) -> ScheduleReceipt:
    if (
        row.workflow_id != workflow_id
        or row.actor_staff_id != str(actor_id)
        or not hmac.compare_digest(row.request_digest, digest)
    ):
        raise HTTPException(409, CONFLICT)
    return _receipt(row, replayed=True)


async def get_command_receipt(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    request_id: UUID,
) -> ScheduleReceipt:
    await _workflow(db, workflow_id, project_id)
    row = await _command(db, project_id, request_id)
    if (
        row is None
        or row.workflow_id != workflow_id
        or row.actor_staff_id != str(actor_id)
    ):
        raise HTTPException(404, "定时计划原请求不存在。")
    return _receipt(row, replayed=True)


def execution_available() -> bool:
    return any(
        node.type == "timer" and node.available
        for node in workflow_capabilities().nodes
    )


async def get_schedule(
    db: AsyncSession, workflow_id: str, project_id: UUID
) -> ScheduleState:
    workflow = await _workflow(db, workflow_id, project_id)
    row = await _schedule(db, workflow_id, project_id)
    current = False
    preview: list[datetime] = []
    if row is not None:
        release = await db.scalar(
            select(WorkflowRelease).where(
                WorkflowRelease.workflow_id == workflow_id,
                WorkflowRelease.project_id == str(project_id),
                WorkflowRelease.id == row.release_id,
                WorkflowRelease.version == workflow.published_version,
            )
        )
        current = bool(
            release is not None
            and workflow.status == "active"
            and release.definition_digest == row.release_digest
            and definition_digest(release.definition) == row.release_digest
        )
        preview = list(
            parse_schedule(row.cron_expression, row.timezone).next_occurrences(
                datetime.now(timezone.utc), count=3
            )
        )
    runtime = await get_timer_runtime_state()
    return ScheduleState(
        runtime=runtime,
        current_release=await _published_timer_context(db, workflow),
        schedule=_summary(row) if row else None,
        binding_current=current,
        execution_available=execution_available() and runtime.ready,
        preview_at=preview,
        suspension_reason=row.suspension_reason if row else None,
        suspended_at=_utc(row.suspended_at) if row and row.suspended_at else None,
    )


async def _apply(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    request: ScheduleSave | ScheduleDisable,
    operation: Literal["save", "disable"],
) -> ScheduleReceipt:
    await _workflow(db, workflow_id, project_id, lock=True)
    digest = _digest(workflow_id, project_id, actor_id, operation, request)
    prior = await _command(db, project_id, request.request_id)
    if prior is not None:
        return _replay(prior, workflow_id, actor_id, digest)
    row = await _schedule(db, workflow_id, project_id)
    if (row.revision if row else 0) != request.expected_revision:
        raise HTTPException(409, CONFLICT)
    now = datetime.now(timezone.utc)
    if isinstance(request, ScheduleSave):
        if request.enabled and not execution_available():
            raise HTTPException(409, "定时派发尚未开放，计划不能启用；可先保存停用配置。")
        if request.enabled and not (await get_timer_runtime_state()).ready:
            raise HTTPException(503, "定时调度、队列消费或数据库探测尚未就绪，请刷新状态后重试。")
        published = await production_definition(
            db, workflow_id, str(project_id)
        )
        verify_execution_binding(
            published,
            request.expected_release_id,
            request.expected_release_digest,
        )
        raw_nodes = published.definition.get("nodes")
        if not isinstance(raw_nodes, list):
            raise HTTPException(409, "固定发布版本的节点定义无法核验。")
        try:
            errors = ValidationService.validate_workflow(
                published.definition,
                allow_timer_preparation=True,
            )
        except (TypeError, ValueError, KeyError):
            raise HTTPException(409, "固定发布版本的配置无法核验。") from None
        timers = [
            node
            for node in raw_nodes
            if isinstance(node, dict) and node.get("type") == "timer"
        ]
        if errors or len(timers) != 1:
            raise HTTPException(409, "固定发布版本必须是配置有效的单一定时工作流。")
        try:
            timer = TimerNodeData.model_validate(timers[0].get("data"))
        except ValidationError:
            raise HTTPException(409, "固定发布版本的定时配置无法核验。") from None
        if row is None:
            row = WorkflowSchedule(
                id=str(uuid4()),
                project_id=str(project_id),
                workflow_id=workflow_id,
                created_by=str(actor_id),
                created_at=now,
            )
            db.add(row)
        row.release_id, row.release_digest = (
            published.release_id,
            request.expected_release_digest,
        )
        row.cron_expression, row.timezone = (
            timer.cron_expression,
            timer.timezone,
        )
        row.enabled = request.enabled
        row.next_run_at = (
            parse_schedule(row.cron_expression, row.timezone).next_occurrences(
                now
            )[0]
            if row.enabled
            else None
        )
    else:
        if row is None:
            raise HTTPException(404, "定时计划不存在。")
        row.enabled, row.next_run_at = False, None
    row.revision = request.expected_revision + 1
    row.suspension_reason, row.suspended_at = None, None
    row.updated_by, row.updated_at = str(actor_id), now
    await db.flush()
    from app.services.timer_state import cancel_superseded
    await cancel_superseded(db, row, now)
    command = WorkflowScheduleCommand(
        project_id=str(project_id),
        request_id=str(request.request_id),
        workflow_id=workflow_id,
        actor_staff_id=str(actor_id),
        operation=operation,
        request_digest=digest,
        result_snapshot=copy_json_object(
            _summary(row).model_dump(mode="json")
        ),
        created_at=now,
    )
    db.add(command)
    # Return a detached reply after commit, even with expire_on_commit=True.
    result = _receipt(command, replayed=False)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await _command(db, project_id, request.request_id)
        if winner is None:
            raise HTTPException(409, CONFLICT) from None
        return _replay(winner, workflow_id, actor_id, digest)
    return result


async def save_schedule(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    request: ScheduleSave,
) -> ScheduleReceipt:
    return await _apply(db, workflow_id, project_id, actor_id, request, "save")


async def disable_schedule(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    request: ScheduleDisable,
) -> ScheduleReceipt:
    return await _apply(
        db, workflow_id, project_id, actor_id, request, "disable"
    )
