"""Human waiting must not consume the resumed worker's execution deadline."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import executions
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import NodeExecution, WorkflowExecution
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services.approval_state import (
    claim_approved_resume,
    decide_approval,
    pause_for_approval,
)
from app.services.event_execution_expiry import expire_internal_event
from app.services.execution_lifecycle import claim_execution
from celery_app import tasks
from tests.unit.test_approval_state import prepared_run, preview_for
from tests.unit.test_approval_worker_resume import approval_definition


@pytest.mark.asyncio
async def test_long_human_wait_does_not_expire_a_newly_resumed_timer():
    (
        engine,
        sessions,
        project,
        execution,
        approver,
        checkpoint,
    ) = await prepared_run()
    now = datetime.now(UTC)
    original_start = now - timedelta(days=2)
    cutoff = now - timedelta(minutes=8)
    try:
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            run.source_channel = "scheduled_timer"
            run.execution_mode = "production"
            run.started_at = original_start
            await db.commit()
            request = await pause_for_approval(
                db,
                project,
                execution,
                "approval",
                approver,
                "确认",
                checkpoint,
                expires_at=now + timedelta(hours=1),
            )
            assert not await expire_internal_event(
                db,
                project,
                execution,
                cutoff,
                now,
            )
            await decide_approval(
                db,
                project,
                request.id,
                approver,
                "approve",
                expected_preview_digest=preview_for(request),
            )
            assert not await expire_internal_event(
                db,
                project,
                execution,
                cutoff,
                now,
            )
            resumed = await claim_approved_resume(
                db,
                project,
                execution,
                run.workflow_id,
                expected_approval_id=request.id,
                expected_approver_id=approver,
            )
            assert resumed == checkpoint
            # Uses the same cutoff a sweep could have captured before claim.
            assert not await expire_internal_event(
                db,
                project,
                execution,
                cutoff,
                now,
            )
            await db.refresh(run)
            assert run.status == "running"
            assert run.started_at.replace(tzinfo=UTC) == original_start
            segment_start = run.active_segment_started_at
            assert segment_start.replace(tzinfo=UTC) >= now
            assert (
                await claim_approved_resume(
                    db,
                    project,
                    execution,
                    run.workflow_id,
                    expected_approval_id=request.id,
                    expected_approver_id=approver,
                )
                is None
            )
            await db.refresh(run)
            assert run.active_segment_started_at == segment_start
            # Approved claims belong to the claim recovery path; the generic
            # source timeout must not destroy their resumable checkpoint.
            later = now + timedelta(minutes=9)
            assert not await expire_internal_event(
                db,
                project,
                execution,
                later - timedelta(minutes=8),
                later,
            )
            await db.refresh(request)
            assert request.checkpoint["capture_status"] != "redacted"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recent_resumes_do_not_hide_overdue_runs_beyond_sweep_limit(
    monkeypatch,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)")
            )
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        async with sessions() as db:
            for index in range(101):
                db.add(
                    WorkflowExecution(
                        id=str(index),
                        project_id="project",
                        workflow_id="flow",
                        source_channel="scheduled_timer",
                        status="running",
                        execution_mode="production",
                        started_at=now - timedelta(days=3),
                        active_segment_started_at=now,
                    )
                )
            db.add(
                WorkflowExecution(
                    id="overdue-legacy",
                    project_id="project",
                    workflow_id="flow",
                    source_channel="internal_event",
                    status="running",
                    execution_mode="production",
                    started_at=now - timedelta(days=2),
                )
            )
            await db.commit()
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        monkeypatch.setattr(tasks.execute_workflow_task, "time_limit", 360)
        assert await tasks.sweep_expired_internal_events(now=now) == 1
        assert await tasks.sweep_expired_internal_events(now=now) == 0
        async with sessions() as db:
            states = (
                await db.scalars(
                    select(
                        WorkflowExecution.status,
                    )
                )
            ).all()
            assert states.count("running") == 101
            assert states.count("failed") == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "internal_event",
        "scheduled_timer",
        "external_webhook",
    ],
)
async def test_atomic_timeout_uses_current_segment_for_every_source(source):
    engine, sessions, project, execution, _, _ = await prepared_run()
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=8)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(WorkflowWebhookDelivery.__table__.create)
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            run.source_channel = source
            run.execution_mode = "production"
            run.started_at = now - timedelta(days=1)
            run.active_segment_started_at = now
            await db.commit()
            assert not await expire_internal_event(
                db,
                project,
                execution,
                cutoff,
                now,
            )
            run.active_segment_started_at = cutoff
            await db.commit()
            assert not await expire_internal_event(
                db,
                "other-project",
                execution,
                cutoff,
                now,
            )
            assert await expire_internal_event(
                db,
                project,
                execution,
                cutoff,
                now,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_initial_duplicate_delivery_does_not_extend_running_segment():
    engine, sessions, project, execution, _, _ = await prepared_run()
    try:
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            run.status = "pending"
            await db.commit()
            assert await claim_execution(
                db,
                execution,
                run.workflow_id,
                project,
            )
            await db.refresh(run)
            started, segment = run.started_at, run.active_segment_started_at
            assert started == segment and segment is not None
            assert not await claim_execution(
                db,
                execution,
                run.workflow_id,
                project,
            )
            await db.refresh(run)
            assert (run.started_at, run.active_segment_started_at) == (
                started,
                segment,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_restarts_segment_for_each_approval_without_replay(
    monkeypatch,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, workflow, actor = (str(uuid4()) for _ in range(3))
    graph = approval_definition(actor)
    second = {
        **graph["nodes"][1],
        "id": "second",
        "data": {**graph["nodes"][1]["data"], "reference_key": "second"},
    }
    graph["nodes"].insert(2, second)
    graph["edges"] = [
        {"source": "start", "target": "approval"},
        {"source": "approval", "target": "second"},
        {"source": "second", "target": "answer"},
    ]
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)")
            )
            for table in (
                WorkflowExecution,
                NodeExecution,
                WorkflowApprovalRequest,
            ):
                await connection.run_sync(table.__table__.create)
        async with sessions() as db:
            run = await executions._create_execution_record(
                db,
                workflow,
                project,
                {},
                status="pending",
                workflow_definition=graph,
                workflow_version=1,
            )
            identifier = run.id
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        monkeypatch.setattr(tasks, "verify_approval_assignees", AsyncMock())
        await tasks._run_workflow(identifier, workflow, {}, project)
        original = datetime.now(UTC) - timedelta(days=2)
        for node in ("approval", "second"):
            async with sessions() as db:
                run = await db.get(WorkflowExecution, identifier)
                assert run.status == "waiting_approval"
                run.started_at = original
                run.active_segment_started_at = original
                await db.commit()
                request = await db.scalar(
                    select(
                        WorkflowApprovalRequest,
                    ).where(
                        WorkflowApprovalRequest.execution_id == identifier,
                        WorkflowApprovalRequest.node_id == node,
                    )
                )
                before_resume = datetime.now(UTC)
                await decide_approval(
                    db,
                    project,
                    request.id,
                    actor,
                    "approve",
                    expected_preview_digest=preview_for(request),
                )
            await tasks._run_workflow(identifier, workflow, {}, project)
            async with sessions() as db:
                run = await db.get(WorkflowExecution, identifier)
                assert run.started_at.replace(tzinfo=UTC) == original
                segment = run.active_segment_started_at
                assert segment.replace(tzinfo=UTC) >= before_resume
            await tasks._run_workflow(identifier, workflow, {}, project)
            async with sessions() as db:
                run = await db.get(WorkflowExecution, identifier)
                assert run.active_segment_started_at == segment
        async with sessions() as db:
            run = await db.get(WorkflowExecution, identifier)
            assert run.status == "completed"
            assert run.input is None and run.output == {"result": "已确认"}
            nodes = (await db.scalars(select(NodeExecution.node_id))).all()
            assert nodes == ["start", "approval", "second", "answer"]
    finally:
        await engine.dispose()
