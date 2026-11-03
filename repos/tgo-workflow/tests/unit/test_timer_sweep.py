"""The periodic task stays inert until enabled, then recovers each due once."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.models.execution import WorkflowExecution
from app.services import node_readiness, timer_dispatch
from celery_app import timer_tasks
from tests.unit.test_timer_dispatch import due_plan
from tests.unit.test_workflow_schedules import schedule_database


@pytest.mark.asyncio
async def test_disabled_sweep_does_not_open_a_database(monkeypatch):
    monkeypatch.setattr(timer_tasks, "timer_capability_enabled", lambda: False)

    def unavailable():
        raise AssertionError("Disabled timer sweep must not open a connection")

    monkeypatch.setattr(timer_tasks, "workflow_task_session", unavailable)
    assert await timer_tasks.sweep_workflow_timers() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_clock", [True, False])
async def test_real_sweep_collects_dispatches_and_does_not_repeat(
    monkeypatch,
    explicit_clock,
):
    monkeypatch.setattr(
        node_readiness,
        "SUPPORTED_NODE_TYPES",
        node_readiness.SUPPORTED_NODE_TYPES | {"timer"},
    )
    monkeypatch.setattr(timer_dispatch, "verify_timer_authority", AsyncMock())
    monkeypatch.setattr(timer_dispatch, "enqueue_pending_initial", AsyncMock())
    async with schedule_database() as fixture:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        await due_plan(fixture, now)
        if not explicit_clock:
            monkeypatch.setattr(
                timer_tasks, "datetime", SimpleNamespace(now=lambda zone: now)
            )
        options = {"now": now} if explicit_clock else {}

        @asynccontextmanager
        async def session():
            async with fixture.sessions() as db:
                yield db

        monkeypatch.setattr(timer_tasks, "workflow_task_session", session)
        assert await timer_tasks.sweep_workflow_timers(**options) == 1
        assert await timer_tasks.sweep_workflow_timers(**options) == 0
        async with fixture.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count()).select_from(WorkflowExecution)
                )
                == 1
            )
