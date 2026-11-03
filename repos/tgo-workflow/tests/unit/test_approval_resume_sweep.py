"""Approved continuations recover after a lost broker acknowledgement."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.services.approval_state import reserve_resume_dispatch
from app.services.workflow_dispatch import WorkflowResumeDispatchError
from celery_app import tasks
from celery_app.celery import celery_app


@pytest.mark.asyncio
async def test_approved_resume_is_redelivered_with_durable_spacing(monkeypatch, tmp_path):
    database_path = (tmp_path / "workflow-resume.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, workflow_id, run_id, approval_id, approver_id = (
        str(uuid4()) for _ in range(5)
    )
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES (:id)"),
                             {"id": workflow_id})
            db.add(WorkflowExecution(
                id=run_id, project_id=project_id, workflow_id=workflow_id,
                status="pending_resume", input={}, active_approval_id=approval_id,
            ))
            db.add(WorkflowApprovalRequest(
                id=approval_id, project_id=project_id, execution_id=run_id,
                node_id="approval", approver_staff_id=approver_id,
                summary="确认", checkpoint={}, status="approved",
                expires_at=now + timedelta(hours=1), decided_at=now,
                decided_by=approver_id,
            ))
            await db.commit()

        # A new engine represents an API/worker process restart; only SQL remains.
        await engine.dispose()
        engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            assert not await reserve_resume_dispatch(
                db, str(uuid4()), approval_id, now=now,
            )
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        enqueue = AsyncMock(side_effect=WorkflowResumeDispatchError("broker unknown"))
        monkeypatch.setattr(tasks, "enqueue_approved_resume", enqueue)

        assert await tasks.sweep_pending_resumes(now=now) == 0
        assert enqueue.await_count == 1
        assert await tasks.sweep_pending_resumes(now=now + timedelta(seconds=30)) == 0
        assert enqueue.await_count == 1

        enqueue.side_effect = None
        assert await tasks.sweep_pending_resumes(now=now + timedelta(minutes=3)) == 1
        assert enqueue.await_count == 2
        async with sessions() as db:
            request = await db.get(WorkflowApprovalRequest, approval_id)
            assert request.resume_dispatch_attempts == 2
            assert request.last_resume_dispatch_at is not None
            run = await db.get(WorkflowExecution, run_id)
            run.status = "cancelled"
            await db.commit()
        async with sessions() as db:
            assert not await reserve_resume_dispatch(
                db, project_id, approval_id, now=now + timedelta(minutes=6),
            )
        assert await tasks.sweep_pending_resumes(now=now + timedelta(minutes=6)) == 0
        assert enqueue.await_count == 2
        assert "resume_pending_workflow_approvals" in celery_app.conf.beat_schedule[
            "resume-pending-workflow-approvals"]["task"]
    finally:
        await engine.dispose()
