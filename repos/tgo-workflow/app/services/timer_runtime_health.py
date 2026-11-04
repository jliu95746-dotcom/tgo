"""Bounded Redis reads and actual read-only worker schema checks."""

import asyncio
from datetime import datetime, timezone
import redis.asyncio as redis
from sqlalchemy import select
from app.config import settings
from app.core.logging import logger
from app.models.execution import WorkflowExecution
from app.models.workflow_schedule import WorkflowSchedule
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.schemas.timer_runtime_health import TimerRuntimeState
from app.services.timer_probe import assess_runtime, probe_key, probe_scope
from celery_app.task_database import workflow_task_session


async def _read_runtime(scope: str) -> TimerRuntimeState:
    async with redis.Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5,
    ) as client:
        values = await client.mget([
            probe_key(scope, "beat"), probe_key(scope, "worker"),
        ])
        now = (await client.time())[0]
    return assess_runtime(values[0], values[1], now=now)


async def get_timer_runtime_state() -> TimerRuntimeState:
    fallback = TimerRuntimeState(
        status="disabled", capability_enabled=settings.TIMER_EXECUTION_ENABLED,
        checked_at=datetime.now(timezone.utc),
    )
    if not settings.TIMER_EXECUTION_ENABLED:
        return fallback
    try:
        scope = probe_scope()
        if scope is None:
            return fallback.model_copy(update={"status": "key_missing"})
        return await asyncio.wait_for(_read_runtime(scope), timeout=1.5)
    except Exception as error:
        logger.warning(
            "Timer runtime check unavailable: %s", type(error).__name__,
        )
        return fallback.model_copy(update={"status": "unavailable"})


async def _check_database() -> None:
    async with workflow_task_session() as db:
        for model in (
            WorkflowSchedule, WorkflowTimerOccurrence, WorkflowExecution,
        ):
            # Check permissions and columns without reading tenant data.
            await db.execute(select(model).limit(0))


async def timer_database_ready() -> bool:
    try:
        await asyncio.wait_for(_check_database(), timeout=3)
        return True
    except Exception as error:
        logger.warning("Timer database probe failed: %s", type(error).__name__)
        return False
