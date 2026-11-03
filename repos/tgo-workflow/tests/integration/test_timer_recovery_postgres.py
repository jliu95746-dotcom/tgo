"""Real PostgreSQL restart-window and bounded backlog acceptance checks."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update

from app.models.execution import NodeExecution, WorkflowExecution
from app.models.workflow_schedule import WorkflowSchedule
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.services import timer_dispatch, timer_runtime, workflow_schedules
from app.services.execution_lifecycle import ExecutionStopped, claim_execution
from app.services.schedule_rules import parse_schedule
from celery_app import timer_tasks
from tests.integration import test_workflow_schedules_postgres as fixtures

database = fixtures.database

pytestmark = pytest.mark.skipif(
    not fixtures.TEST_URL, reason="Disposable PostgreSQL database required",
)


@pytest.mark.asyncio
async def test_year_long_interruption_preserves_one_skipped_fact(
    database, monkeypatch,
):
    engine, _ = database
    sessions, _, _, plan_id, now = await fixtures.seed_due(engine)
    async with sessions() as db:
        plan = await db.get(WorkflowSchedule, plan_id)
        rule = parse_schedule(plan.cron_expression, plan.timezone)
        due = rule.next_occurrences(now - timedelta(days=366))[0]
        plan.next_run_at = due
        await db.commit()

    async def collect():
        async with sessions() as db:
            return await timer_dispatch.materialize_due(db, plan_id, now=now)

    collected = await asyncio.gather(*(collect() for _ in range(5)))
    identifiers = [identifier for identifier in collected if identifier]
    assert len(identifiers) == 1
    verify, enqueue = AsyncMock(), AsyncMock()
    monkeypatch.setattr(timer_dispatch, "verify_timer_authority", verify)
    monkeypatch.setattr(timer_dispatch, "enqueue_pending_initial", enqueue)
    async with sessions() as db:
        fact = await db.get(WorkflowTimerOccurrence, identifiers[0])
        assert fact.scheduled_at == due and fact.status == "skipped"
        assert fact.error_code == "schedule_missed_window"
        assert await timer_dispatch.dispatch_occurrence(
            db, fact.id, now=now,
        ) is None
        plan = await db.get(WorkflowSchedule, plan_id)
        assert plan.next_run_at == rule.next_occurrences(now)[0]
        # A clock rewind cannot create a second fact for the persisted due.
        plan.next_run_at = due
        await db.commit()
        assert await timer_dispatch.materialize_due(
            db, plan_id, now=now,
        ) == fact.id
        assert await db.scalar(
            select(func.count()).select_from(WorkflowTimerOccurrence),
        ) == 1
        assert await db.scalar(
            select(func.count()).select_from(WorkflowExecution),
        ) == 0
    verify.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_unstarted_queued_work_expires_before_any_node_receipt(
    database, monkeypatch,
):
    engine, _ = database
    sessions, project, flow, plan_id, now = await fixtures.seed_due(engine)
    due = now - timedelta(minutes=10)
    monkeypatch.setattr(timer_dispatch, "verify_timer_authority", AsyncMock())
    enqueue = AsyncMock()
    monkeypatch.setattr(timer_dispatch, "enqueue_pending_initial", enqueue)
    monkeypatch.setattr(timer_runtime, "verify_timer_authority", AsyncMock())
    async with sessions() as db:
        await db.execute(update(WorkflowSchedule).where(
            WorkflowSchedule.id == plan_id,
        ).values(next_run_at=due))
        await db.commit()
        # Only the historical source clock is synthetic; this is not broker QA.
        occurrence = await timer_dispatch.materialize_due(db, plan_id, now=due)
        identifier = await timer_dispatch.dispatch_occurrence(
            db, occurrence, now=due,
        )
        assert identifier is not None
        assert await claim_execution(db, identifier, flow, str(project))
        with pytest.raises(ExecutionStopped):
            await timer_runtime.require_active_timer(
                db, str(project), flow, identifier,
            )
    enqueue.assert_awaited_once()
    async with sessions() as db:
        fact = await db.get(WorkflowTimerOccurrence, occurrence)
        run = await db.get(WorkflowExecution, identifier)
        assert fact.status == "skipped" and fact.claimed_at is None
        assert fact.error_code == "schedule_missed_window"
        assert run.status == "failed"
        assert run.input is None and run.output is None
        assert await db.scalar(
            select(func.count()).select_from(NodeExecution),
        ) == 0
        assert not await claim_execution(db, identifier, flow, str(project))


@pytest.mark.asyncio
async def test_backlog_is_bounded_and_advances_across_multiple_sweeps(
    database, monkeypatch,
):
    engine, _ = database
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    for _ in range(23):
        sessions, project, actor, flow, request = await fixtures.seed(engine)
        async with sessions() as db:
            receipt = await workflow_schedules.save_schedule(
                db, flow, project, actor, request,
            )
            await db.execute(update(WorkflowSchedule).where(
                WorkflowSchedule.id == str(receipt.schedule.id),
            ).values(enabled=True, next_run_at=now - timedelta(days=365)))
            await db.commit()

    @asynccontextmanager
    async def session():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(timer_tasks, "workflow_task_session", session)
    verify, enqueue = AsyncMock(), AsyncMock()
    monkeypatch.setattr(timer_dispatch, "verify_timer_authority", verify)
    monkeypatch.setattr(timer_dispatch, "enqueue_pending_initial", enqueue)
    for expected in (10, 20, 23, 23):
        assert await timer_tasks.sweep_workflow_timers(now=now) == 0
        async with sessions() as db:
            facts = list(await db.scalars(select(WorkflowTimerOccurrence)))
            assert len(facts) == expected
            assert all(fact.status == "skipped" for fact in facts)
            assert await db.scalar(
                select(func.count()).select_from(WorkflowExecution),
            ) == 0
    async with sessions() as db:
        plans = list(await db.scalars(select(WorkflowSchedule)))
        assert len(plans) == 23
        assert all(plan.next_run_at > now for plan in plans)
    verify.assert_not_awaited()
    enqueue.assert_not_awaited()
