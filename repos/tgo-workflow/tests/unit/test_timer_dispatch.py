"""Timer due facts survive retries without creating a second run."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update

from app.models.execution import WorkflowExecution
from app.models.workflow_schedule import WorkflowSchedule
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.schemas.workflow_schedule import ScheduleDisable
from app.services import timer_dispatch as dispatch
from app.services import workflow_schedules
from app.services.workflow_dispatch import WorkflowInitialDispatchError
from tests.unit.test_workflow_schedules import (
    Fixture,
    request,
    schedule_database,
)
import pytest_asyncio


@pytest_asyncio.fixture
async def fixture(monkeypatch):
    from app.services import node_readiness

    monkeypatch.setattr(
        node_readiness,
        "SUPPORTED_NODE_TYPES",
        node_readiness.SUPPORTED_NODE_TYPES | {"timer"},
    )
    async with schedule_database() as result:
        yield result


async def due_plan(fixture: Fixture, now: datetime, *, age: int = 0) -> str:
    async with fixture.sessions() as db:
        saved = await workflow_schedules.save_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            request(fixture),
        )
        await db.execute(
            update(WorkflowSchedule).values(
                enabled=True, next_run_at=now - timedelta(seconds=age)
            )
        )
        await db.commit()
        return str(saved.schedule.id)


@pytest.mark.asyncio
async def test_due_materialization_is_stable_and_advances_the_clock(
    fixture: Fixture,
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    plan = await due_plan(fixture, now)
    async with fixture.sessions() as db:
        first = await dispatch.materialize_due(db, plan, now=now)
        assert first is not None
        assert await dispatch.materialize_due(db, plan, now=now) is None
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowTimerOccurrence)
            )
            == 1
        )
        row = await db.get(WorkflowSchedule, plan)
        assert row.next_run_at.replace(tzinfo=timezone.utc) > now
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowExecution)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_disabled_capability_never_collects_or_contacts_authority(
    fixture: Fixture, monkeypatch
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    plan = await due_plan(fixture, now)
    monkeypatch.setattr(dispatch, "timer_capability_enabled", lambda: False)
    verify = AsyncMock()
    monkeypatch.setattr(dispatch, "verify_timer_authority", verify)
    async with fixture.sessions() as db:
        assert await dispatch.materialize_due(db, plan, now=now) is None
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowTimerOccurrence)
            )
            == 0
        )
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_due_time_is_recorded_once_without_catchup_execution(
    fixture: Fixture,
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    plan = await due_plan(fixture, now, age=1800)
    async with fixture.sessions() as db:
        identifier = await dispatch.materialize_due(db, plan, now=now)
        row = await db.get(WorkflowTimerOccurrence, identifier)
        assert row.status == "skipped"
        assert row.error_code == "schedule_missed_window"
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowExecution)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_lost_broker_ack_keeps_one_committed_run(
    fixture: Fixture, monkeypatch
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    plan = await due_plan(fixture, now)
    verify = AsyncMock()
    enqueue = AsyncMock(side_effect=WorkflowInitialDispatchError("unknown"))
    monkeypatch.setattr(dispatch, "verify_timer_authority", verify)
    monkeypatch.setattr(dispatch, "enqueue_pending_initial", enqueue)
    async with fixture.sessions() as db:
        identifier = await dispatch.materialize_due(db, plan, now=now)
        execution = await dispatch.dispatch_occurrence(db, identifier, now=now)
        assert execution is not None
        assert (
            await dispatch.dispatch_occurrence(db, identifier, now=now) is None
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowExecution)
            )
            == 1
        )
        run = await db.get(WorkflowExecution, execution)
        assert run.status == "pending"
        assert run.source_channel == "scheduled_timer"
        assert run.input["scheduled_at"] == now.isoformat()
        assert (
            await db.get(WorkflowTimerOccurrence, identifier)
        ).status == "queued"
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_disable_while_authority_waits_prevents_late_dispatch(
    fixture: Fixture, monkeypatch
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    plan = await due_plan(fixture, now)

    async def stop(query):
        async with fixture.sessions() as other:
            from uuid import uuid4

            await workflow_schedules.disable_schedule(
                other,
                fixture.workflow_id,
                fixture.project_id,
                fixture.actor_id,
                ScheduleDisable(request_id=uuid4(), expected_revision=1),
            )

    monkeypatch.setattr(dispatch, "verify_timer_authority", stop)
    enqueue = AsyncMock()
    monkeypatch.setattr(dispatch, "enqueue_pending_initial", enqueue)
    async with fixture.sessions() as db:
        identifier = await dispatch.materialize_due(db, plan, now=now)
        assert (
            await dispatch.dispatch_occurrence(db, identifier, now=now) is None
        )
        assert (
            await db.get(WorkflowTimerOccurrence, identifier)
        ).status == "cancelled"
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowExecution)
            )
            == 0
        )
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_revocation_suspends_without_enqueuing(
    fixture: Fixture, monkeypatch
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    plan = await due_plan(fixture, now)
    monkeypatch.setattr(
        dispatch,
        "verify_timer_authority",
        AsyncMock(side_effect=PermissionError()),
    )
    async with fixture.sessions() as db:
        identifier = await dispatch.materialize_due(db, plan, now=now)
        assert (
            await dispatch.dispatch_occurrence(db, identifier, now=now) is None
        )
        assert (
            await db.get(WorkflowTimerOccurrence, identifier)
        ).status == "blocked"
        assert (
            await db.get(WorkflowSchedule, plan)
        ).suspension_reason == "schedule_authority_revoked"
        assert (
            await dispatch.materialize_due(
                db, plan, now=now + timedelta(days=1)
            )
            is None
        )


@pytest.mark.asyncio
async def test_temporary_authority_failure_retries_the_same_fact(
    fixture: Fixture, monkeypatch
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    plan = await due_plan(fixture, now)
    verify = AsyncMock(side_effect=[RuntimeError(), None])
    monkeypatch.setattr(dispatch, "verify_timer_authority", verify)
    monkeypatch.setattr(dispatch, "enqueue_pending_initial", AsyncMock())
    async with fixture.sessions() as db:
        identifier = await dispatch.materialize_due(db, plan, now=now)
        assert (
            await dispatch.dispatch_occurrence(db, identifier, now=now) is None
        )
        row = await db.get(WorkflowTimerOccurrence, identifier)
        assert (
            row.status == "pending"
            and row.last_failure_code == "schedule_authority_unavailable"
        )
        result = await dispatch.dispatch_occurrence(
            db, identifier, now=now + timedelta(seconds=31)
        )
        assert result is not None
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowTimerOccurrence)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowExecution)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_old_authority_reply_cannot_overwrite_a_newer_claim(
    fixture: Fixture, monkeypatch
):
    import asyncio

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    plan = await due_plan(fixture, now)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def verify(query):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()

    monkeypatch.setattr(dispatch, "verify_timer_authority", verify)
    monkeypatch.setattr(dispatch, "enqueue_pending_initial", AsyncMock())
    async with fixture.sessions() as db:
        identifier = await dispatch.materialize_due(db, plan, now=now)
    async with fixture.sessions() as old:
        earlier = asyncio.create_task(
            dispatch.dispatch_occurrence(old, identifier, now=now)
        )
        await asyncio.wait_for(started.wait(), timeout=3)
        async with fixture.sessions() as later:
            winner = await dispatch.dispatch_occurrence(
                later, identifier, now=now + timedelta(seconds=31)
            )
        release.set()
        assert await earlier is None
        assert winner is not None
    async with fixture.sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowExecution)
            )
            == 1
        )
