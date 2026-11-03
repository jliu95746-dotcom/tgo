"""A committed employee approval run survives a crash before broker publish."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.execution import WorkflowExecution
from app.services.execution_snapshot import definition_digest
from app.services.execution_lifecycle import claim_execution
from app.services.workflow_dispatch import (
    WorkflowInitialDispatchError, enqueue_pending_initial,
)
from celery_app import tasks
from celery_app.celery import celery_app


@pytest.mark.asyncio
async def test_recovery_publish_uses_same_task_id_and_keeps_pending_on_unknown_ack(monkeypatch):
    row = SimpleNamespace(id="run-1", workflow_id="workflow-1", source_channel=None,
                          project_id="project-1", input={"question": "private"})
    publish = Mock(side_effect=OSError("private-broker-url"))
    monkeypatch.setattr(tasks.execute_workflow_task, "apply_async", publish)
    with pytest.raises(WorkflowInitialDispatchError) as error:
        await enqueue_pending_initial(row)
    assert "private-broker-url" not in str(error.value)
    assert publish.call_args.kwargs["task_id"] == "run-1"
    assert publish.call_args.kwargs["retry"] is False
    assert publish.call_args.kwargs["kwargs"] == {"project_id": "project-1"}


@pytest.mark.asyncio
async def test_initial_employee_dispatch_recovers_after_restart(monkeypatch, tmp_path):
    database_path = (tmp_path / "initial-dispatch.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, workflow_id, employee_id = (str(uuid4()) for _ in range(3))
    run_id, draft_id, cancelled_id = (str(uuid4()) for _ in range(3))
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES (:id)"),
                             {"id": workflow_id})
            db.add_all([
                WorkflowExecution(
                    id=run_id, project_id=project_id, workflow_id=workflow_id,
                    status="pending", execution_mode="production",
                    employee_agent_id=employee_id, release_id=str(uuid4()),
                    definition_snapshot={"nodes": [], "edges": []},
                    definition_digest=definition_digest({"nodes": [], "edges": []}),
                    workflow_version=1,
                    input={"question": "test"},
                    started_at=now - timedelta(minutes=3),
                ),
                WorkflowExecution(
                    id=draft_id, project_id=project_id, workflow_id=workflow_id,
                    status="pending", execution_mode="draft_test", input={},
                    started_at=now - timedelta(minutes=3),
                ),
                WorkflowExecution(
                    id=cancelled_id, project_id=project_id, workflow_id=workflow_id,
                    status="cancelled", execution_mode="production",
                    employee_agent_id=employee_id, release_id=str(uuid4()), input={},
                    started_at=now - timedelta(minutes=3),
                ),
            ])
            await db.commit()

        await engine.dispose()
        engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        enqueue = AsyncMock(side_effect=tasks.WorkflowInitialDispatchError("unknown"))
        monkeypatch.setattr(tasks, "enqueue_pending_initial", enqueue)

        assert await tasks.sweep_pending_initial(now=now) == 0
        assert enqueue.await_count == 1
        assert enqueue.await_args.args[0].id == run_id
        async with sessions() as observed:
            assert (await observed.get(WorkflowExecution, run_id)).status == "pending"
        assert await tasks.sweep_pending_initial(now=now + timedelta(minutes=1)) == 0
        assert enqueue.await_count == 1

        enqueue.side_effect = None
        assert await tasks.sweep_pending_initial(now=now + timedelta(minutes=8)) == 1
        assert enqueue.await_count == 2
        async with sessions() as db:
            run = await db.get(WorkflowExecution, run_id)
            assert run.initial_dispatch_attempts == 2
            assert run.last_initial_dispatch_at is not None
            assert await claim_execution(db, run_id, workflow_id, project_id)
            assert not await claim_execution(db, run_id, workflow_id, project_id)
        assert await tasks.sweep_pending_initial(now=now + timedelta(minutes=15)) == 0
        assert "recover_pending_initial_workflows" in celery_app.conf.beat_schedule[
            "recover-pending-initial-workflows"]["task"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_unknown_delivery_slows_without_failing_pending_run(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'exhausted.db').as_posix()}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, workflow_id, run_id = (str(uuid4()) for _ in range(3))
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES (:id)"),
                             {"id": workflow_id})
            db.add(WorkflowExecution(
                id=run_id, project_id=project_id, workflow_id=workflow_id,
                status="pending", execution_mode="production",
                employee_agent_id=str(uuid4()), release_id=str(uuid4()),
                definition_snapshot={"nodes": [], "edges": []}, input={},
                definition_digest=definition_digest({"nodes": [], "edges": []}),
                workflow_version=1,
                initial_dispatch_attempts=12,
                last_initial_dispatch_at=now - timedelta(minutes=6),
                started_at=now - timedelta(hours=2),
            ))
            await db.commit()
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        enqueue = AsyncMock()
        monkeypatch.setattr(tasks, "enqueue_pending_initial", enqueue)
        assert await tasks.sweep_pending_initial(now=now) == 0
        enqueue.assert_not_awaited()
        async with sessions() as db:
            run = await db.get(WorkflowExecution, run_id)
            assert run.status == "pending"
        assert await tasks.sweep_pending_initial(now=now + timedelta(minutes=55)) == 1
        enqueue.assert_awaited_once()
        async with sessions() as db:
            run = await db.get(WorkflowExecution, run_id)
            assert run.status == "pending"
            assert run.initial_dispatch_attempts == 13
    finally:
        await engine.dispose()
