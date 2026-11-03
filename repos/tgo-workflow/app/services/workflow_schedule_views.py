"""Owned previews and bounded source history without execution payloads."""

from datetime import datetime, timezone
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.execution import WorkflowExecution
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.schemas.workflow_schedule_views import (
    ScheduleOccurrence,
    ScheduleOccurrenceList,
    SchedulePreview,
    SchedulePreviewRequest,
    ScheduleReleaseContext,
)
from app.schemas.node import TimerNodeData
from app.services.execution_snapshot import definition_digest
from app.services.validation_service import ValidationService
from app.services.schedule_rules import ScheduleRuleError, parse_schedule
from app.services.timer_state import execution_id as timer_execution_id, utc


async def published_timer_context(
    db: AsyncSession,
    workflow: Workflow,
) -> ScheduleReleaseContext | None:
    if workflow.status != "active" or workflow.published_version is None:
        return None
    release = await db.scalar(
        select(WorkflowRelease).where(
            WorkflowRelease.workflow_id == workflow.id,
            WorkflowRelease.project_id == workflow.project_id,
            WorkflowRelease.version == workflow.published_version,
        )
    )
    if release is None:
        return None
    try:
        if definition_digest(release.definition) != release.definition_digest:
            return None
        if ValidationService.validate_workflow(
            release.definition, allow_timer_preparation=True
        ):
            return None
        nodes = release.definition.get("nodes")
        if not isinstance(nodes, list):
            return None
        timers = [
            node
            for node in nodes
            if (isinstance(node, dict) and node.get("type") == "timer")
        ]
        if len(timers) != 1:
            return None
        timer = TimerNodeData.model_validate(timers[0].get("data"))
    except (ValueError, TypeError, KeyError):
        return None
    return ScheduleReleaseContext(
        release_id=release.id,
        definition_digest=release.definition_digest,
        version=release.version,
        cron_expression=timer.cron_expression,
        timezone=timer.timezone,
    )


async def require_owned_workflow(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
) -> None:
    found = await db.scalar(
        select(Workflow.id).where(
            Workflow.id == workflow_id,
            Workflow.project_id == str(project_id),
        )
    )
    if found is None:
        raise HTTPException(404, "工作流不存在。")


async def preview_schedule(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    request: SchedulePreviewRequest,
) -> SchedulePreview:
    await require_owned_workflow(db, workflow_id, project_id)
    now = datetime.now(timezone.utc)
    try:
        times = parse_schedule(
            request.cron_expression, request.timezone
        ).next_occurrences(now, count=5)
    except ScheduleRuleError as error:
        raise HTTPException(400, str(error)) from None
    return SchedulePreview(
        cron_expression=" ".join(request.cron_expression.split()),
        timezone=request.timezone,
        computed_at=now,
        preview_at=list(times),
    )


async def list_occurrences(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    limit: int,
    offset: int,
) -> ScheduleOccurrenceList:
    await require_owned_workflow(db, workflow_id, project_id)
    owned = (
        WorkflowTimerOccurrence.workflow_id == workflow_id,
        WorkflowTimerOccurrence.project_id == str(project_id),
    )
    total = (
        await db.scalar(
            select(func.count())
            .select_from(WorkflowTimerOccurrence)
            .where(*owned)
        )
        or 0
    )
    rows = await db.execute(
        select(
            WorkflowTimerOccurrence,
            WorkflowExecution.id,
            WorkflowExecution.status,
        )
        .outerjoin(
            WorkflowExecution,
            and_(
                WorkflowExecution.id == WorkflowTimerOccurrence.execution_id,
                WorkflowExecution.workflow_id == workflow_id,
                WorkflowExecution.project_id == str(project_id),
                WorkflowExecution.source_channel == "scheduled_timer",
                WorkflowExecution.execution_mode == "production",
                WorkflowExecution.release_id
                == WorkflowTimerOccurrence.release_id,
                WorkflowExecution.definition_digest
                == WorkflowTimerOccurrence.release_digest,
                WorkflowExecution.source_message_id
                == "timer-" + WorkflowTimerOccurrence.id,
            ),
        )
        .where(*owned)
        .order_by(
            WorkflowTimerOccurrence.scheduled_at.desc(),
            WorkflowTimerOccurrence.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    items = [
        ScheduleOccurrence(
            id=UUID(row.id),
            revision=row.revision,
            scheduled_at=utc(row.scheduled_at),
            status=row.status,
            attempts=row.attempts,
            error_code=row.error_code,
            last_failure_code=row.last_failure_code,
            execution_id=run_id
            if run_id == timer_execution_id(row.id)
            else None,
            execution_status=run_status
            if run_id == timer_execution_id(row.id)
            else None,
            created_at=utc(row.created_at),
        )
        for row, run_id, run_status in rows
    ]
    return ScheduleOccurrenceList(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )
