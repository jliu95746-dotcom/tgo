"""Owned timer bindings and cancellation, serialized by the workflow row."""

from datetime import datetime, timedelta, timezone
import json
from uuid import NAMESPACE_URL, UUID, uuid5
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.execution import WorkflowExecution
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_schedule import WorkflowSchedule
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.schemas.node import TimerNodeData
from app.schemas.timer_authority import TimerAuthority
from app.services.execution_snapshot import definition_digest
from app.services.validation_service import ValidationService
from app.services.approval_retention import scrub_terminal_approval_checkpoints

START_WINDOW = timedelta(minutes=5)
STOP_MESSAGE = "定时计划已停用、修订或失去权限，任务已停止。"


def timer_capability_enabled() -> bool:
    from app.services.node_readiness import workflow_capabilities

    return any(
        node.type == "timer" and node.available
        for node in workflow_capabilities().nodes
    )


def utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def occurrence_id(schedule_id: str, revision: int, due: datetime) -> str:
    scope = json.dumps(
        [schedule_id, revision, utc(due).isoformat()], separators=(",", ":")
    )
    return str(uuid5(NAMESPACE_URL, "tgo-timer-occurrence:v1:" + scope))


def execution_id(identifier: str) -> str:
    return str(uuid5(NAMESPACE_URL, "tgo-timer-run:v1:" + identifier))


def authority(row: WorkflowTimerOccurrence) -> TimerAuthority:
    return TimerAuthority(
        project_id=UUID(row.project_id),
        actor_staff_id=UUID(row.actor_staff_id),
        workflow_id=row.workflow_id,
        schedule_id=UUID(row.schedule_id),
        revision=row.revision,
        occurrence_id=UUID(row.id),
        scheduled_at=utc(row.scheduled_at),
        release_id=row.release_id,
        release_digest=row.release_digest,
    )


async def lock_workflow(
    db: AsyncSession, workflow_id: str, project_id: str
) -> Workflow | None:
    return await db.scalar(
        select(Workflow)
        .where(
            Workflow.id == workflow_id,
            Workflow.project_id == project_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def lock_occurrence(
    db: AsyncSession, identifier: str
) -> WorkflowTimerOccurrence | None:
    hint = await db.get(WorkflowTimerOccurrence, identifier)
    if (
        hint is None
        or await lock_workflow(db, hint.workflow_id, hint.project_id) is None
    ):
        return None
    return await db.scalar(
        select(WorkflowTimerOccurrence)
        .where(
            WorkflowTimerOccurrence.id == identifier,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def binding_error(
    db: AsyncSession, row: WorkflowTimerOccurrence
) -> str | None:
    if row.id != occurrence_id(
        row.schedule_id, row.revision, row.scheduled_at
    ):
        return "schedule_source_changed"
    plan = await db.get(
        WorkflowSchedule, row.schedule_id, populate_existing=True
    )
    workflow = await db.get(Workflow, row.workflow_id, populate_existing=True)
    if (
        plan is None
        or workflow is None
        or plan.project_id != row.project_id
        or plan.workflow_id != row.workflow_id
        or workflow.project_id != row.project_id
    ):
        return "schedule_owner_changed"
    if (
        not plan.enabled
        or plan.revision != row.revision
        or plan.updated_by != row.actor_staff_id
    ):
        return "schedule_revision_changed"
    if plan.suspension_reason:
        return plan.suspension_reason
    release = await db.get(
        WorkflowRelease, row.release_id, populate_existing=True
    )
    if (
        workflow.status != "active"
        or release is None
        or release.project_id != row.project_id
        or release.workflow_id != row.workflow_id
        or workflow.published_version != release.version
        or plan.release_id != row.release_id
        or plan.release_digest != row.release_digest
        or release.definition_digest != row.release_digest
    ):
        return "schedule_release_changed"
    try:
        if definition_digest(release.definition) != row.release_digest:
            return "schedule_release_changed"
        if ValidationService.validate_workflow(
            release.definition, allow_timer_preparation=True
        ):
            return "schedule_definition_invalid"
    except (ValueError, TypeError, KeyError):
        return "schedule_definition_invalid"
    nodes = release.definition.get("nodes")
    if not isinstance(nodes, list):
        return "schedule_definition_invalid"
    timers = [
        node
        for node in nodes
        if isinstance(node, dict) and node.get("type") == "timer"
    ]
    try:
        if len(timers) != 1:
            return "schedule_definition_invalid"
        timer = TimerNodeData.model_validate(timers[0].get("data"))
        if (
            timer.cron_expression != plan.cron_expression
            or timer.timezone != plan.timezone
        ):
            return "schedule_definition_invalid"
    except ValueError:
        return "schedule_definition_invalid"
    return None


async def suspend(
    db: AsyncSession, row: WorkflowTimerOccurrence, code: str, now: datetime
) -> None:
    await db.execute(
        update(WorkflowSchedule)
        .where(
            WorkflowSchedule.id == row.schedule_id,
            WorkflowSchedule.project_id == row.project_id,
            WorkflowSchedule.revision == row.revision,
            WorkflowSchedule.enabled.is_(True),
        )
        .values(suspension_reason=code, suspended_at=now)
    )


async def cancel_superseded(
    db: AsyncSession, plan: WorkflowSchedule, now: datetime
) -> None:
    live_runs = select(WorkflowExecution.id).where(
        WorkflowExecution.project_id == plan.project_id,
        WorkflowExecution.workflow_id == plan.workflow_id,
        WorkflowExecution.source_channel == "scheduled_timer",
        WorkflowExecution.status.in_(
            (
                "pending",
                "running",
                "waiting_approval",
                "pending_resume",
            )
        ),
    )
    rows = (
        await db.scalars(
            select(WorkflowTimerOccurrence).where(
                WorkflowTimerOccurrence.project_id == plan.project_id,
                WorkflowTimerOccurrence.schedule_id == plan.id,
                WorkflowTimerOccurrence.revision < plan.revision,
                or_(
                    WorkflowTimerOccurrence.status == "pending",
                    and_(
                        WorkflowTimerOccurrence.status == "queued",
                        WorkflowTimerOccurrence.execution_id.in_(live_runs),
                    ),
                ),
            )
        )
    ).all()
    identifiers = [row.execution_id for row in rows if row.execution_id]
    if identifiers:
        changed = await db.scalars(
            update(WorkflowExecution)
            .where(
                WorkflowExecution.id.in_(identifiers),
                WorkflowExecution.project_id == plan.project_id,
                WorkflowExecution.source_channel == "scheduled_timer",
                WorkflowExecution.status.in_(
                    (
                        "pending",
                        "running",
                        "waiting_approval",
                        "pending_resume",
                    )
                ),
            )
            .values(
                status="cancelled",
                input=None,
                output=None,
                error=STOP_MESSAGE,
                completed_at=now,
            )
            .returning(WorkflowExecution.id)
        )
        for identifier in changed.all():
            await scrub_terminal_approval_checkpoints(
                db,
                plan.project_id,
                identifier,
            )
    for row in rows:
        row.status, row.error_code, row.updated_at = (
            "cancelled",
            "schedule_revision_changed",
            now,
        )
