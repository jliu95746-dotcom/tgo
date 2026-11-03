"""No timer node or result may outlive its current plan authorization."""

from datetime import datetime, timezone
from typing import NoReturn
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.integrations.timer_authority_client import verify_timer_authority
from app.models.execution import WorkflowExecution
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.services.execution_lifecycle import ExecutionStopped
from app.services.approval_retention import scrub_terminal_approval_checkpoints
from app.services.timer_state import (
    START_WINDOW,
    STOP_MESSAGE,
    authority,
    binding_error,
    execution_id,
    lock_occurrence,
    suspend,
    timer_capability_enabled,
    utc,
)


async def _stop(
    db: AsyncSession, run: WorkflowExecution, *, failed: bool = False
) -> NoReturn:
    await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == run.id,
            WorkflowExecution.project_id == run.project_id,
            WorkflowExecution.source_channel == "scheduled_timer",
            WorkflowExecution.status == "running",
        )
        .values(
            status="failed" if failed else "cancelled",
            input=None,
            output=None,
            error=STOP_MESSAGE,
            completed_at=datetime.now(timezone.utc),
        )
    )
    await scrub_terminal_approval_checkpoints(db, run.project_id, run.id)
    await db.commit()
    raise ExecutionStopped(STOP_MESSAGE)


async def require_active_timer(
    db: AsyncSession,
    project_id: str,
    workflow_id: str,
    identifier: str,
    *,
    retain_lock: bool = False,
) -> None:
    run = await db.scalar(
        select(WorkflowExecution)
        .where(
            WorkflowExecution.id == identifier,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.workflow_id == workflow_id,
        )
        .execution_options(populate_existing=True)
    )
    if run is None or run.status != "running":
        raise ExecutionStopped(STOP_MESSAGE)
    if not timer_capability_enabled():
        await _stop(db, run, failed=True)
    row = await db.scalar(
        select(WorkflowTimerOccurrence)
        .where(
            WorkflowTimerOccurrence.execution_id == identifier,
            WorkflowTimerOccurrence.project_id == project_id,
            WorkflowTimerOccurrence.workflow_id == workflow_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        row is None
        or row.status != "queued"
        or identifier != execution_id(row.id)
    ):
        await _stop(db, run)
    if (
        row is None
        or run.source_channel != "scheduled_timer"
        or run.execution_mode != "production"
        or run.release_id != row.release_id
        or run.definition_digest != row.release_digest
        or run.source_message_id != "timer-" + row.id
        or not isinstance(run.input, dict)
        or run.input.get("_timer_occurrence_id") != row.id
        or run.input.get("scheduled_at") != utc(row.scheduled_at).isoformat()
    ):
        await _stop(db, run)
    query = authority(row)
    await db.commit()
    permission: str | None = None
    try:
        await verify_timer_authority(query)
    except PermissionError:
        permission = "schedule_authority_revoked"
    except RuntimeError:
        permission = "schedule_authority_unavailable"
    row = await lock_occurrence(db, str(query.occurrence_id))
    run = await db.get(WorkflowExecution, identifier, populate_existing=True)
    if run is None or run.status != "running":
        await db.rollback()
        raise ExecutionStopped(STOP_MESSAGE)
    if row is None or row.status != "queued":
        await _stop(db, run)
    current = datetime.now(timezone.utc)
    error = await binding_error(db, row)
    late = (
        row.claimed_at is None
        and current - utc(row.scheduled_at) > START_WINDOW
    )
    if permission or error or late:
        row.error_code = permission or error or "schedule_missed_window"
        if late:
            row.status = "skipped"
        elif permission == "schedule_authority_revoked":
            row.status = "blocked"
        if permission == "schedule_authority_revoked":
            await suspend(db, row, permission, current)
        await _stop(
            db,
            run,
            failed=permission == "schedule_authority_unavailable" or late,
        )
    if row.claimed_at is None:
        row.claimed_at = current
    if not retain_lock:
        await db.commit()
