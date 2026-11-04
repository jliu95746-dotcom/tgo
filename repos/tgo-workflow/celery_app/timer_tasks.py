"""Bounded periodic collection and recovery for durable timer sources."""

import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from app.core.logging import logger
from app.models.workflow_schedule import WorkflowSchedule
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.services.timer_dispatch import materialize_due, dispatch_occurrence
from app.services.timer_state import timer_capability_enabled
from celery_app.celery import celery_app
from celery_app.task_database import workflow_task_session


@celery_app.task(name="sweep_workflow_timers")
def sweep_workflow_timers_task() -> int:
    return asyncio.run(sweep_workflow_timers())


async def sweep_workflow_timers(*, now: datetime | None = None) -> int:
    if not timer_capability_enabled():
        return 0
    current = now or datetime.now(timezone.utc)
    async with workflow_task_session() as db:
        plans = list(
            await db.scalars(
                select(WorkflowSchedule.id)
                .where(
                    WorkflowSchedule.enabled.is_(True),
                    WorkflowSchedule.suspension_reason.is_(None),
                    WorkflowSchedule.next_run_at <= current,
                )
                .order_by(WorkflowSchedule.next_run_at, WorkflowSchedule.id)
                .limit(10)
            )
        )
    for identifier in plans:
        try:
            async with workflow_task_session() as db:
                await materialize_due(db, identifier, now=current)
        except Exception as error:
            logger.error(
                "Timer source collection failed: %s", type(error).__name__
            )
    async with workflow_task_session() as db:
        pending = list(
            await db.scalars(
                select(WorkflowTimerOccurrence.id)
                .where(
                    WorkflowTimerOccurrence.status == "pending",
                    WorkflowTimerOccurrence.next_attempt_at <= current,
                )
                .order_by(
                    WorkflowTimerOccurrence.created_at,
                    WorkflowTimerOccurrence.id,
                )
                .limit(10)
            )
        )
    delivered = 0
    for identifier in pending:
        try:
            async with workflow_task_session() as db:
                if await dispatch_occurrence(db, identifier, now=now):
                    delivered += 1
        except Exception as error:
            logger.error("Timer dispatch failed: %s", type(error).__name__)
    return delivered
