"""Freeze due facts before RPC or broker I/O, then fence every late reply."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.timer_authority_client import verify_timer_authority
from app.models.execution import WorkflowExecution
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_schedule import WorkflowSchedule
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.schemas.timer_authority import TimerAuthority
from app.services.execution_snapshot import copy_json_object
from app.services.schedule_rules import parse_schedule
from app.services.timer_state import (
    START_WINDOW,
    authority,
    binding_error,
    execution_id,
    lock_occurrence,
    lock_workflow,
    occurrence_id,
    suspend,
    timer_capability_enabled,
    utc,
)
from app.services.workflow_dispatch import (
    WorkflowInitialDispatchError,
    enqueue_pending_initial,
)


@dataclass(frozen=True)
class TimerClaim:
    query: TimerAuthority
    attempt: int


async def materialize_due(
    db: AsyncSession, plan_id: str, *, now: datetime | None = None
) -> str | None:
    if not timer_capability_enabled():
        return None
    current = utc(now or datetime.now(timezone.utc))
    hint = await db.get(WorkflowSchedule, plan_id)
    if (
        hint is None
        or await lock_workflow(db, hint.workflow_id, hint.project_id) is None
    ):
        return None
    plan = await db.get(WorkflowSchedule, plan_id, populate_existing=True)
    if (
        plan is None
        or not plan.enabled
        or plan.suspension_reason
        or plan.next_run_at is None
        or utc(plan.next_run_at) > current
    ):
        return None
    due = utc(plan.next_run_at)
    identifier = occurrence_id(plan.id, plan.revision, due)
    prior = await db.get(WorkflowTimerOccurrence, identifier)
    if prior is not None:
        # A partial/manual clock rewind cannot manufacture another source.
        plan.next_run_at = parse_schedule(
            plan.cron_expression, plan.timezone
        ).next_occurrences(current)[0]
        await db.commit()
        return identifier
    row = WorkflowTimerOccurrence(
        id=identifier,
        project_id=plan.project_id,
        workflow_id=plan.workflow_id,
        schedule_id=plan.id,
        revision=plan.revision,
        scheduled_at=due,
        actor_staff_id=plan.updated_by,
        release_id=plan.release_id,
        release_digest=plan.release_digest,
        status="pending",
        attempts=0,
        next_attempt_at=current,
        created_at=current,
        updated_at=current,
    )
    db.add(row)
    error = await binding_error(db, row)
    if error:
        row.status, row.error_code = "blocked", error
        await suspend(db, row, error, current)
    else:
        plan.next_run_at = parse_schedule(
            plan.cron_expression, plan.timezone
        ).next_occurrences(current)[0]
        if current - due > START_WINDOW:
            row.status, row.error_code = "skipped", "schedule_missed_window"
    await db.commit()
    return identifier


async def _claim(
    db: AsyncSession, identifier: str, current: datetime
) -> TimerClaim | None:
    row = await lock_occurrence(db, identifier)
    if (
        row is None
        or row.status != "pending"
        or utc(row.next_attempt_at) > current
    ):
        return None
    error = await binding_error(db, row)
    if error:
        row.status, row.error_code, row.updated_at = (
            "cancelled",
            error,
            current,
        )
        await db.commit()
        return None
    if current - utc(row.scheduled_at) > START_WINDOW or row.attempts >= 8:
        row.status, row.error_code, row.updated_at = (
            "skipped",
            "schedule_missed_window",
            current,
        )
        await db.commit()
        return None
    row.attempts += 1
    row.next_attempt_at = current + timedelta(seconds=30)
    row.updated_at = current
    claim = TimerClaim(authority(row), row.attempts)
    # Release the workflow lock before checking API-owned permission.
    await db.commit()
    return claim


async def dispatch_occurrence(
    db: AsyncSession, identifier: str, *, now: datetime | None = None
) -> str | None:
    if not timer_capability_enabled():
        return None
    current = utc(now or datetime.now(timezone.utc))
    claim = await _claim(db, identifier, current)
    if claim is None:
        return None
    denial: str | None = None
    try:
        await verify_timer_authority(claim.query)
    except PermissionError:
        denial = "schedule_authority_revoked"
    except RuntimeError:
        denial = "schedule_authority_unavailable"
    finished = utc(now or datetime.now(timezone.utc))
    row = await lock_occurrence(db, identifier)
    if row is None or row.status != "pending" or row.attempts != claim.attempt:
        return None
    error = await binding_error(db, row)
    if error:
        row.status, row.error_code = "cancelled", error
    elif finished - utc(row.scheduled_at) > START_WINDOW:
        row.status, row.error_code = "skipped", "schedule_missed_window"
    elif denial == "schedule_authority_revoked":
        row.status, row.error_code = "blocked", denial
        row.last_failure_code = denial
        await suspend(db, row, denial, finished)
    elif denial:
        row.error_code = denial
        row.last_failure_code = denial
        row.next_attempt_at = finished + timedelta(
            seconds=min(120, 5 * 2 ** min(row.attempts, 5))
        )
    else:
        release = await db.get(WorkflowRelease, row.release_id)
        plan = await db.get(WorkflowSchedule, row.schedule_id)
        if release is None or plan is None:
            row.status, row.error_code = "blocked", "schedule_owner_changed"
            await db.commit()
            return None
        identifier_run = execution_id(row.id)
        run = WorkflowExecution(
            id=identifier_run,
            project_id=row.project_id,
            workflow_id=row.workflow_id,
            status="pending",
            input={
                "scheduled_at": utc(row.scheduled_at).isoformat(),
                "timezone": plan.timezone,
                "_timer_occurrence_id": row.id,
            },
            definition_snapshot=copy_json_object(release.definition),
            workflow_version=release.version,
            definition_digest=row.release_digest,
            execution_mode="production",
            release_id=row.release_id,
            source_channel="scheduled_timer",
            source_message_id="timer-" + row.id,
            workflow_lineage=[row.workflow_id],
            started_at=finished,
        )
        db.add(run)
        row.execution_id, row.status, row.error_code = (
            identifier_run,
            "queued",
            None,
        )
        row.updated_at = finished
        await db.commit()
        try:
            await enqueue_pending_initial(run)
        except WorkflowInitialDispatchError:
            await db.execute(
                update(WorkflowTimerOccurrence)
                .where(
                    WorkflowTimerOccurrence.id == identifier,
                    WorkflowTimerOccurrence.execution_id == identifier_run,
                    WorkflowTimerOccurrence.status == "queued",
                )
                .values(last_failure_code="schedule_broker_ack_unknown")
            )
            await db.commit()
        return identifier_run
    row.updated_at = finished
    await db.commit()
    return None
