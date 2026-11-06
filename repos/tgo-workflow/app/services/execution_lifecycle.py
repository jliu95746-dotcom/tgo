"""Atomic state changes keep duplicate and late tasks from reviving a run."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import JsonValue
from sqlalchemy import and_, or_, select, update
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import WorkflowExecution
from app.services.webhook_lifecycle import scrub_webhook_inputs
from app.services.approval_retention import scrub_terminal_approval_checkpoints
from app.services.event_execution_expiry import (
    SOURCE_EVENT_PENDING_RETENTION,
    expire_pending_source_event,
)

DISPATCH_FAILURE = "工作流任务未能提交，请稍后重试。"
INITIAL_DISPATCH_GRACE = timedelta(minutes=2)
INITIAL_DISPATCH_INTERVAL = timedelta(minutes=5)
INITIAL_DISPATCH_SLOW_INTERVAL = timedelta(hours=1)
INITIAL_DISPATCH_FAST_ATTEMPTS = 12


def initial_dispatch_due(current: datetime) -> ColumnElement[bool]:
    """Retry slowly after repeated unknown broker acknowledgements."""
    return or_(
        WorkflowExecution.last_initial_dispatch_at.is_(None),
        and_(
            WorkflowExecution.initial_dispatch_attempts
            < INITIAL_DISPATCH_FAST_ATTEMPTS,
            WorkflowExecution.last_initial_dispatch_at
            <= current - INITIAL_DISPATCH_INTERVAL,
        ),
        and_(
            WorkflowExecution.initial_dispatch_attempts
            >= INITIAL_DISPATCH_FAST_ATTEMPTS,
            WorkflowExecution.last_initial_dispatch_at
            <= current - INITIAL_DISPATCH_SLOW_INTERVAL,
        ),
    )


class ExecutionStopped(RuntimeError):
    """The persisted run is no longer eligible to execute another node."""


async def claim_execution(
    db: AsyncSession,
    execution_id: str,
    workflow_id: str,
    project_id: str,
) -> bool:
    now = datetime.now(timezone.utc)
    source_cutoff = now - SOURCE_EVENT_PENDING_RETENTION
    result = await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.workflow_id == workflow_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "pending",
            or_(
                WorkflowExecution.source_channel.is_distinct_from(
                    "internal_event"
                ),
                WorkflowExecution.execution_mode.is_distinct_from(
                    "production"
                ),
                WorkflowExecution.visitor_session_id.is_(None),
                WorkflowExecution.started_at > source_cutoff,
            ),
        )
        .values(
            status="running",
            started_at=now,
            active_segment_started_at=now,
            error=None,
        )
        .returning(WorkflowExecution.id)
    )
    claimed = result.scalar_one_or_none() is not None
    await db.commit()
    if not claimed:
        await expire_pending_source_event(
            db, project_id, execution_id, source_cutoff, now
        )
    return claimed


async def fail_pending_dispatch(
    db: AsyncSession,
    execution_id: str,
    project_id: str,
) -> bool:
    result = await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "pending",
        )
        .values(
            status="failed",
            input=None,
            error=DISPATCH_FAILURE,
            completed_at=datetime.now(timezone.utc),
            duration=0,
        )
        .returning(WorkflowExecution.id)
    )
    changed = result.scalar_one_or_none() is not None
    if changed:
        await scrub_terminal_approval_checkpoints(db, project_id, execution_id)
        await scrub_webhook_inputs(db, project_id, execution_id)
    await db.commit()
    return changed


async def reserve_initial_dispatch(
    db: AsyncSession,
    project_id: str,
    execution_id: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Reserve one redelivery across workers before contacting the broker."""
    current = now or datetime.now(timezone.utc)
    changed = await db.scalar(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "pending",
            WorkflowExecution.execution_mode == "production",
            or_(
                WorkflowExecution.employee_agent_id.is_not(None),
                WorkflowExecution.source_channel.in_(
                    ("internal_event", "scheduled_timer", "external_webhook")
                ),
            ),
            WorkflowExecution.release_id.is_not(None),
            WorkflowExecution.definition_snapshot.is_not(None),
            WorkflowExecution.definition_digest.is_not(None),
            WorkflowExecution.workflow_version.is_not(None),
            WorkflowExecution.started_at <= current - INITIAL_DISPATCH_GRACE,
            initial_dispatch_due(current),
        )
        .values(
            last_initial_dispatch_at=current,
            initial_dispatch_attempts=(
                WorkflowExecution.initial_dispatch_attempts + 1
            ),
        )
        .returning(WorkflowExecution.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return changed is not None


async def require_running(
    db: AsyncSession,
    execution_id: str,
    project_id: str,
    *,
    expected_segment_started_at: datetime | None = None,
) -> None:
    if expected_segment_started_at is not None:
        owned = await db.scalar(
            select(WorkflowExecution.id).where(
                WorkflowExecution.id == execution_id,
                WorkflowExecution.project_id == project_id,
                WorkflowExecution.status == "running",
                WorkflowExecution.active_segment_started_at
                == expected_segment_started_at,
            )
        )
        await db.commit()
        if owned is None:
            raise ExecutionStopped("工作流执行归属已变化，不再执行后续节点。")
        return
    if await read_execution_status(db, execution_id, project_id) != "running":
        raise ExecutionStopped("工作流已停止，不再执行后续节点。")


async def read_execution_status(
    db: AsyncSession,
    execution_id: str,
    project_id: str,
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
    db: AsyncSession,
    execution_id: str,
    project_id: str,
) -> None:
    changed = await db.scalar(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "running",
        )
        .values(
            status="cancelled",
            input=None,
            completed_at=datetime.now(timezone.utc),
        )
        .returning(WorkflowExecution.id)
    )
    if changed is not None:
        await scrub_terminal_approval_checkpoints(db, project_id, execution_id)
        await scrub_webhook_inputs(db, project_id, execution_id)
    await db.commit()


async def finish_running_execution(
    db: AsyncSession,
    execution_id: str,
    project_id: str,
    status: Literal["completed", "failed"],
    output: JsonValue,
    error: str | None,
    duration: int,
    *,
    expected_segment_started_at: datetime | None = None,
) -> bool:
    conditions = [
        WorkflowExecution.id == execution_id,
        WorkflowExecution.project_id == project_id,
        WorkflowExecution.status == "running",
    ]
    if expected_segment_started_at is not None:
        conditions.append(
            WorkflowExecution.active_segment_started_at
            == expected_segment_started_at
        )
    result = await db.execute(
        update(WorkflowExecution)
        .where(*conditions)
        .values(
            status=status,
            input=None,
            output=output,
            error=error,
            active_approval_id=None,
            duration=duration,
            completed_at=datetime.now(timezone.utc),
        )
        .returning(WorkflowExecution.id)
    )
    changed = result.scalar_one_or_none() is not None
    if changed:
        await scrub_terminal_approval_checkpoints(db, project_id, execution_id)
        await scrub_webhook_inputs(db, project_id, execution_id)
    await db.commit()
    return changed
