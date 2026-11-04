"""The real engine uses SQL inputs and checks authority at node boundaries."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from app.models.execution import WorkflowExecution, NodeExecution
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.services import timer_dispatch as dispatch, timer_runtime
from app.services.execution_snapshot import copy_json_object, definition_digest
from celery_app import tasks
from tests.unit.test_timer_dispatch import due_plan
from tests.unit.test_workflow_schedules import Fixture, schedule_database


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


async def queued(fixture: Fixture, monkeypatch, now: datetime) -> str:
    async with fixture.sessions() as db:
        release = await db.get(WorkflowRelease, fixture.release_id)
        graph = copy_json_object(release.definition)
        graph["nodes"][1]["data"]["output_template"] = "{{clock.timestamp}}"
        fixture.digest = definition_digest(graph)
        await db.execute(
            update(WorkflowRelease).values(
                definition=graph, definition_digest=fixture.digest
            )
        )
        await db.execute(update(Workflow).values(definition=graph))
        await db.commit()
    plan = await due_plan(fixture, now)
    monkeypatch.setattr(dispatch, "verify_timer_authority", AsyncMock())
    monkeypatch.setattr(dispatch, "enqueue_pending_initial", AsyncMock())
    async with fixture.sessions() as db:
        identifier = await dispatch.materialize_due(db, plan, now=now)
        run = await dispatch.dispatch_occurrence(db, identifier, now=now)

    @asynccontextmanager
    async def session():
        async with fixture.sessions() as db:
            yield db

    monkeypatch.setattr(tasks, "workflow_task_session", session)
    return run


@pytest.mark.asyncio
async def test_real_worker_uses_frozen_due_time_and_ignores_broker_injection(
    fixture: Fixture, monkeypatch
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    identifier = await queued(fixture, monkeypatch, now)
    verify = AsyncMock()
    monkeypatch.setattr(timer_runtime, "verify_timer_authority", verify)
    await tasks._run_workflow(
        identifier,
        fixture.workflow_id,
        {"scheduled_at": "FORGED"},
        str(fixture.project_id),
    )
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        assert run.status == "completed"
        assert run.input is None
        assert now.isoformat() in json.dumps(run.output)
        assert "FORGED" not in json.dumps(run.output)
    assert verify.await_count >= 3


@pytest.mark.asyncio
async def test_revocation_between_nodes_suppresses_final_output(
    fixture: Fixture, monkeypatch
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    identifier = await queued(fixture, monkeypatch, now)
    verify = AsyncMock(side_effect=[None, None, PermissionError()])
    monkeypatch.setattr(timer_runtime, "verify_timer_authority", verify)
    await tasks._run_workflow(
        identifier, fixture.workflow_id, {}, str(fixture.project_id)
    )
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        assert run.status == "cancelled"
        assert run.input is None and run.output is None


@pytest.mark.asyncio
async def test_late_worker_cannot_start_a_model_or_node(
    fixture: Fixture, monkeypatch
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    identifier = await queued(fixture, monkeypatch, now)

    class Late(datetime):
        @classmethod
        def now(cls, tz=None):
            return now + timedelta(minutes=10)

    monkeypatch.setattr(timer_runtime, "datetime", Late)
    monkeypatch.setattr(timer_runtime, "verify_timer_authority", AsyncMock())
    await tasks._run_workflow(
        identifier, fixture.workflow_id, {}, str(fixture.project_id)
    )
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        assert run.status == "failed"
        assert run.output is None and run.input is None
        assert (
            await db.scalar(select(func.count()).select_from(NodeExecution))
            == 0
        )


@pytest.mark.asyncio
async def test_disabled_engine_stops_an_already_queued_timer(
    fixture: Fixture, monkeypatch
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    identifier = await queued(fixture, monkeypatch, now)
    monkeypatch.setattr(
        timer_runtime, "timer_capability_enabled", lambda: False
    )
    verify = AsyncMock()
    monkeypatch.setattr(timer_runtime, "verify_timer_authority", verify)
    await tasks._run_workflow(
        identifier, fixture.workflow_id, {}, str(fixture.project_id)
    )
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        assert (
            run.status == "failed" and run.output is None and run.input is None
        )
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_crashed_timer_run_expires_without_replaying_it(
    fixture: Fixture, monkeypatch
):
    from app.services.event_execution_expiry import expire_internal_event

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    identifier = await queued(fixture, monkeypatch, now)
    async with fixture.sessions() as db:
        await db.execute(
            update(WorkflowExecution).values(
                status="running", started_at=now - timedelta(minutes=10)
            )
        )
        await db.commit()
        assert await expire_internal_event(
            db,
            str(fixture.project_id),
            identifier,
            now - timedelta(minutes=8),
            now,
        )
        run = await db.get(
            WorkflowExecution, identifier, populate_existing=True
        )
        assert (
            run.status == "failed" and run.input is None and run.output is None
        )
