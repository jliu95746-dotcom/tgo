"""Queued automatic events cannot keep excerpts or start work indefinitely."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.execution import WorkflowExecution
from app.services.event_execution_expiry import expire_pending_source_event
from app.services.execution_lifecycle import claim_execution
from app.services.workflow_dispatch import enqueue_pending_initial
from celery_app import tasks
from celery_app.celery import celery_app


@pytest.mark.asyncio
async def test_sweep_clears_only_expired_pending_source_inputs(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    old = now - timedelta(minutes=30)
    variants = {
        "expired": {}, "recent": {"started_at": old + timedelta(seconds=1)},
        "running": {"status": "running"}, "manual": {"visitor_session_id": None},
        "draft": {"execution_mode": "draft_test"},
        "employee": {"source_channel": "web"},
        "completed": {"status": "completed", "output": {"result": "keep"}},
    }
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
        async with sessions() as db:
            for run_id, overrides in variants.items():
                fields = {"id": run_id, "project_id": "project", "workflow_id": "flow",
                    "status": "pending", "execution_mode": "production",
                    "source_channel": "internal_event", "visitor_session_id": "session",
                    "input": {"data": {"excerpt": "private"}}, "started_at": old}
                fields.update(overrides)
                db.add(WorkflowExecution(**fields))
            await db.commit()
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        enqueue = AsyncMock()
        monkeypatch.setattr(tasks, "enqueue_pending_initial", enqueue)
        assert await tasks.sweep_pending_source_expiry(now=now) == 1
        assert await tasks.sweep_pending_source_expiry(now=now) == 0
        enqueue.assert_not_awaited()
        async with sessions() as db:
            expired = await db.get(WorkflowExecution, "expired")
            assert expired.status == "failed" and expired.input is None
            assert expired.output is None and expired.completed_at
            assert not await claim_execution(db, "expired", "flow", "project")
            for run_id, overrides in variants.items():
                if run_id == "expired":
                    continue
                other = await db.get(WorkflowExecution, run_id)
                assert other.status == overrides.get("status", "pending")
                assert other.input == {"data": {"excerpt": "private"}}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expiry_rechecks_tenant_and_worker_claim_before_clearing():
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=30)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
        async with sessions() as db:
            run = WorkflowExecution(id="run", project_id="project", workflow_id="flow",
                status="pending", source_channel="internal_event", execution_mode="production",
                visitor_session_id="session", started_at=cutoff, input={"data": {}})
            db.add(run)
            await db.commit()
            assert not await expire_pending_source_event(db, "other", "run", cutoff, now)
            # Claim a still-recent task, then emulate a delayed expiry writer.
            run.started_at = now
            await db.commit()
            assert await claim_execution(db, "run", "flow", "project")
            assert not await expire_pending_source_event(
                db, "project", "run", now + timedelta(minutes=1), now + timedelta(minutes=31),
            )
            await db.refresh(run)
            assert run.status == "running" and run.input is not None
    finally:
        await engine.dispose()


def test_pending_source_expiry_is_scheduled_independently_of_execution_timeout():
    schedule = celery_app.conf.beat_schedule["expire-pending-source-inputs"]
    assert schedule["task"] == "expire_pending_source_inputs"
    assert schedule["schedule"] == 60.0


@pytest.mark.asyncio
async def test_late_worker_clears_expired_source_before_the_periodic_sweep():
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    old = datetime.now(timezone.utc) - timedelta(minutes=31)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
        async with sessions() as db:
            for run_id, channel in (("expired", "internal_event"), ("legacy", None)):
                db.add(WorkflowExecution(id=run_id, project_id="project", workflow_id="flow",
                    status="pending", source_channel=channel, execution_mode="production",
                    visitor_session_id="session", started_at=old, input={"data": {}}))
            await db.commit()
            assert not await claim_execution(db, "expired", "flow", "project")
            expired = await db.get(WorkflowExecution, "expired")
            assert expired.status == "failed" and expired.input is None
            assert await claim_execution(db, "legacy", "flow", "project")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_channel", ["internal_event", "web"])
async def test_event_queue_uses_database_snapshot_without_copying_customer_body(monkeypatch, source_channel):
    publish = MagicMock()
    monkeypatch.setattr(tasks.execute_workflow_task, "apply_async", publish)
    run = WorkflowExecution(id="run", project_id="project", workflow_id="flow",
        source_channel=source_channel, input={"data": {"excerpt": "private body"}})
    await enqueue_pending_initial(run)
    sent = publish.call_args.kwargs
    assert sent["args"][:2] == ["run", "flow"]
    assert sent["args"][2] == ({} if source_channel == "internal_event" else run.input)
