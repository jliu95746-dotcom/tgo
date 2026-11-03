"""End overdue event runs without replaying potentially billed model calls."""

from datetime import datetime, timedelta
from math import isfinite

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.execution import WorkflowExecution
from app.services.webhook_lifecycle import scrub_webhook_inputs
from app.services.approval_retention import scrub_terminal_approval_checkpoints

EVENT_EXECUTION_GRACE_SECONDS = 120
EVENT_EXECUTION_TIMEOUT = "事件任务已超过最长执行时间，已停止；请核对运行记录后重新发起。"
SOURCE_EVENT_PENDING_RETENTION = timedelta(minutes=30)
SOURCE_EVENT_PENDING_TIMEOUT = "自动事件等待执行超过 30 分钟，片段输入已清理；请核查队列状态。"


def running_segment_started_at() -> ColumnElement[datetime]:
    """Keep old runs readable without guessing their historical claim time."""
    return func.coalesce(
        WorkflowExecution.active_segment_started_at,
        WorkflowExecution.started_at,
    )


def event_execution_cutoff(
    now: datetime, hard_time_limit: object,
) -> datetime | None:
    """A missing/disabled Celery limit does not prove a run is overdue."""
    if (
        isinstance(hard_time_limit, bool)
        or not isinstance(hard_time_limit, (int, float))
        or not isfinite(hard_time_limit)
        or hard_time_limit <= 0
    ):
        return None
    try:
        return now - timedelta(
            seconds=hard_time_limit + EVENT_EXECUTION_GRACE_SECONDS,
        )
    except OverflowError:
        return None


async def expire_internal_event(
    db: AsyncSession, project_id: str, execution_id: str,
    cutoff: datetime, now: datetime,
) -> bool:
    """Recheck eligibility atomically; a late worker cannot revive this run."""
    changed = await db.scalar(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.source_channel.in_(
                ("internal_event", "scheduled_timer", "external_webhook"),
            ),
            WorkflowExecution.execution_mode == "production",
            WorkflowExecution.status == "running",
            running_segment_started_at() <= cutoff,
        )
        .values(
            status="failed", input=None, output=None,
            error=EVENT_EXECUTION_TIMEOUT, completed_at=now,
        )
        .returning(WorkflowExecution.id)
        .execution_options(synchronize_session=False)
    )
    if changed is not None:
        await scrub_terminal_approval_checkpoints(db, project_id, execution_id)
        await scrub_webhook_inputs(db, project_id, execution_id)
    await db.commit()
    return changed is not None


async def expire_pending_source_event(
    db: AsyncSession, project_id: str, execution_id: str,
    cutoff: datetime, now: datetime,
) -> bool:
    """Expire unclaimed events; a concurrent worker claim wins safely."""
    changed = await db.scalar(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.source_channel == "internal_event",
            WorkflowExecution.execution_mode == "production",
            WorkflowExecution.visitor_session_id.is_not(None),
            WorkflowExecution.status == "pending",
            WorkflowExecution.started_at <= cutoff,
        )
        .values(
            status="failed", input=None, output=None, duration=0,
            error=SOURCE_EVENT_PENDING_TIMEOUT, completed_at=now,
        )
        .returning(WorkflowExecution.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return changed is not None
