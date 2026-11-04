"""Overdue internal events stop explicitly without replaying model calls."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.execution import WorkflowExecution
from app.models.approval import WorkflowApprovalRequest
from app.services.event_execution_expiry import (
    EVENT_EXECUTION_TIMEOUT, event_execution_cutoff, expire_internal_event,
)
from app.services.execution_lifecycle import (
    claim_execution, finish_running_execution, require_running, ExecutionStopped,
)
from celery_app import tasks
from celery_app.celery import celery_app


@pytest.mark.parametrize("limit", [None, False, True, 0, -1, float("nan"), float("inf")])
def test_missing_or_invalid_hard_limit_never_expires_runs(limit):
    assert event_execution_cutoff(datetime.now(timezone.utc), limit) is None


def test_cutoff_uses_hard_limit_and_grace():
    now = datetime.now(timezone.utc)
    assert event_execution_cutoff(now, 360) == now - timedelta(seconds=480)


@pytest.mark.asyncio
async def test_sweep_after_restart_expires_only_overdue_running_events(monkeypatch, tmp_path):
    path = (tmp_path / "event-expiry.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=480)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        variants = {
            "overdue": {},
            "recent": {"started_at": old + timedelta(seconds=1)},
            "pending": {"status": "pending"},
            "waiting": {"status": "waiting_approval"},
            "resume": {"status": "pending_resume"},
            "complete": {"status": "completed", "output": {"result": "keep"}},
            "cancelled": {"status": "cancelled"},
            "draft": {"execution_mode": "draft_test"},
            "employee": {"source_channel": "web", "employee_agent_id": "employee-1"},
        }
        async with sessions() as db:
            for run_id, overrides in variants.items():
                values = {
                    "id": run_id, "project_id": "project-1", "workflow_id": "workflow-1",
                    "status": "running", "execution_mode": "production",
                    "source_channel": "internal_event", "input": {"data": {"private": "input"}},
                    "started_at": old,
                }
                values.update(overrides)
                db.add(WorkflowExecution(**values))
            await db.commit()
        await engine.dispose()
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        monkeypatch.setattr(tasks.execute_workflow_task, "time_limit", 360)
        monkeypatch.setattr(celery_app.conf, "task_time_limit", 60)
        enqueue = AsyncMock()
        monkeypatch.setattr(tasks, "enqueue_pending_initial", enqueue)

        assert await tasks.sweep_expired_internal_events(now=now) == 1
        assert await tasks.sweep_expired_internal_events(now=now) == 0
        enqueue.assert_not_awaited()
        async with sessions() as db:
            failed = await db.get(WorkflowExecution, "overdue")
            assert failed.status == "failed"
            assert failed.input is None and failed.output is None
            assert failed.error == EVENT_EXECUTION_TIMEOUT
            assert failed.completed_at is not None
            for run_id, overrides in variants.items():
                if run_id != "overdue":
                    run = await db.get(WorkflowExecution, run_id)
                    assert run.status == overrides.get("status", "running")
            assert (await db.get(WorkflowExecution, "complete")).output == {"result": "keep"}
            assert not await claim_execution(db, "overdue", "workflow-1", "project-1")
            with pytest.raises(ExecutionStopped):
                await require_running(db, "overdue", "project-1")
            assert not await finish_running_execution(
                db, "overdue", "project-1", "completed", {"result": "late"}, None, 1,
            )
            await db.refresh(failed)
            assert failed.status == "failed" and failed.output is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expiry_rechecks_scope_status_and_claim_time(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'race.db').as_posix()}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=8)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        async with sessions() as db:
            db.add(WorkflowExecution(
                id="run", project_id="project", workflow_id="workflow", status="running",
                source_channel="internal_event", execution_mode="production",
                started_at=cutoff - timedelta(seconds=1), input={"data": {}},
            ))
            await db.commit()
            assert not await expire_internal_event(db, "other-project", "run", cutoff, now)
            run = await db.get(WorkflowExecution, "run")
            run.started_at = now
            await db.commit()
            assert not await expire_internal_event(db, "project", "run", cutoff, now)
            run.started_at = cutoff - timedelta(seconds=1)
            run.status = "completed"
            run.output = {"result": "completed-before-expiry"}
            await db.commit()
            assert not await expire_internal_event(db, "project", "run", cutoff, now)
            await db.refresh(run)
            assert run.output == {"result": "completed-before-expiry"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sweep_respects_effective_task_limit_and_disabled_limit(monkeypatch):
    sessions = AsyncMock()
    monkeypatch.setattr(tasks, "workflow_task_session", sessions)
    monkeypatch.setattr(tasks.execute_workflow_task, "time_limit", None)
    monkeypatch.setattr(celery_app.conf, "task_time_limit", None)
    assert await tasks.sweep_expired_internal_events() == 0
    sessions.assert_not_called()


def test_expiry_sweep_is_scheduled():
    schedule = celery_app.conf.beat_schedule["expire-running-internal-events"]
    assert schedule["task"] == "expire_running_internal_events"
    assert schedule["schedule"] == 60.0
