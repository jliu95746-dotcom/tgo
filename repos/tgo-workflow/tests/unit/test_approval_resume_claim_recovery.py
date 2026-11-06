"""Recover an interrupted claim only before any successor action starts."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import NodeExecution, WorkflowExecution
from app.services.approval_preview import approval_preview_digest
from app.services.approval_resume_recovery import (
    begin_resume_node,
    finish_resume_node,
    recover_interrupted_approval,
)
from app.services.approval_state import (
    claim_approved_resume,
    decide_approval,
    pause_for_approval,
)
from app.services.event_execution_expiry import expire_internal_event
from app.services.execution_lifecycle import (
    ExecutionStopped,
    finish_running_execution,
    require_running,
)
from celery_app import approval_recovery_tasks, tasks
from celery_app.celery import celery_app
from tests.unit.test_approval_state import prepared_run


async def claimed():
    engine, sessions, project, execution, actor, checkpoint = await prepared_run()
    async with engine.begin() as connection:
        await connection.run_sync(NodeExecution.__table__.create)
    async with sessions() as db:
        request = await pause_for_approval(
            db,
            project,
            execution,
            "approval",
            actor,
            "确认",
            checkpoint,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await decide_approval(
            db,
            project,
            request.id,
            actor,
            "approve",
            expected_preview_digest=approval_preview_digest(
                request.id,
                execution,
                request.summary,
            ),
        )
        flow = await db.get(WorkflowExecution, execution)
        assert await claim_approved_resume(
            db,
            project,
            execution,
            flow.workflow_id,
            expected_approval_id=request.id,
            expected_approver_id=actor,
        )
        await db.refresh(flow)
        token = flow.active_segment_started_at
        saved = dict(request.checkpoint)
        return engine, sessions, project, execution, actor, request.id, token, saved


@pytest.mark.asyncio
async def test_interrupted_claim_returns_to_original_encrypted_approval():
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        token,
        saved,
    ) = await claimed()
    try:
        async with sessions() as db:
            assert (
                await recover_interrupted_approval(
                    db,
                    project,
                    execution,
                    cutoff=datetime.now(UTC) + timedelta(seconds=1),
                )
                == "requeued"
            )
            run = await db.get(WorkflowExecution, execution)
            request = await db.get(WorkflowApprovalRequest, approval)
            assert run.status == "pending_resume"
            assert request.status == "approved" and request.checkpoint == saved
            recovered = await claim_approved_resume(
                db,
                project,
                execution,
                run.workflow_id,
                expected_approval_id=approval,
                expected_approver_id=actor,
            )
            assert recovered is not None and recovered.completed_nodes == ["approval"]
            await db.refresh(run)
            assert run.active_segment_started_at != token
            assert not (await db.scalars(select(NodeExecution))).all()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_started_action_is_retained_for_reconciliation_without_replay():
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        token,
        saved,
    ) = await claimed()
    try:
        async with sessions() as db:
            await begin_resume_node(db, project, execution, "business", "tool", token)
            assert (
                await recover_interrupted_approval(
                    db,
                    project,
                    execution,
                    cutoff=datetime.now(UTC) + timedelta(seconds=1),
                )
                == "reconciliation_required"
            )
            run = await db.get(WorkflowExecution, execution)
            request = await db.get(WorkflowApprovalRequest, approval)
            assert run.status == "running" and "核验" in run.error
            assert run.active_segment_started_at is None
            with pytest.raises(ExecutionStopped):
                await begin_resume_node(db, project, execution, "late", "tool", token)
            with pytest.raises(ExecutionStopped):
                await require_running(
                    db,
                    execution,
                    project,
                    expected_segment_started_at=token,
                )
            await db.refresh(run)
            await db.refresh(request)
            assert request.checkpoint == saved
            assert (
                await claim_approved_resume(
                    db,
                    project,
                    execution,
                    run.workflow_id,
                    expected_approval_id=approval,
                    expected_approver_id=actor,
                )
                is None
            )
            assert len((await db.scalars(select(NodeExecution))).all()) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["internal_event", "scheduled_timer", "external_webhook"]
)
async def test_source_timeout_cannot_discard_approved_recovery(source):
    engine, sessions, project, execution, _, approval, _, saved = await claimed()
    now = datetime.now(UTC) + timedelta(hours=1)
    try:
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            run.source_channel = source
            run.execution_mode = "production"
            await db.commit()
            assert not await expire_internal_event(
                db,
                project,
                execution,
                now - timedelta(minutes=8),
                now,
            )
            assert (
                await recover_interrupted_approval(
                    db,
                    project,
                    execution,
                    cutoff=now - timedelta(minutes=8),
                )
                == "requeued"
            )
            request = await db.get(WorkflowApprovalRequest, approval)
            assert request.checkpoint == saved
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_receipts_require_current_owner_scope_and_unfinished_intent():
    engine, sessions, project, execution, _, _, token, _ = await claimed()
    try:
        async with sessions() as db:
            for scope, segment in [(str(uuid4()), token), (project, None)]:
                with pytest.raises(ExecutionStopped):
                    await begin_resume_node(
                        db, scope, execution, "answer", "answer", segment
                    )
            receipt = await begin_resume_node(
                db, project, execution, "answer", "answer", token
            )
            with pytest.raises(ValueError):
                await finish_resume_node(
                    db,
                    project,
                    execution,
                    receipt,
                    token,
                    status="running",
                    inputs={},
                    output=None,
                    error=None,
                    duration=0,
                )
            with pytest.raises(ExecutionStopped):
                await finish_resume_node(
                    db,
                    project,
                    execution,
                    str(uuid4()),
                    token,
                    status="completed",
                    inputs={},
                    output=None,
                    error=None,
                    duration=0,
                )
            await finish_resume_node(
                db,
                project,
                execution,
                receipt,
                token,
                status="completed",
                inputs={"query": "synthetic"},
                output={"answer": "synthetic"},
                error=None,
                duration=12,
            )
            row = await db.get(NodeExecution, receipt)
            assert row.status == "completed" and row.duration == 12
            assert row.input == {"query": "synthetic"}
            assert row.output == {"answer": "synthetic"} and row.completed_at
            with pytest.raises(ExecutionStopped):
                await finish_resume_node(
                    db,
                    project,
                    execution,
                    receipt,
                    token,
                    status="completed",
                    inputs={},
                    output="late",
                    error=None,
                    duration=0,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_receipt_retains_no_business_result():
    engine, sessions, project, execution, _, _, token, _ = await claimed()
    try:
        async with sessions() as db:
            receipt = await begin_resume_node(
                db, project, execution, "answer", "answer", token
            )
            run = await db.get(WorkflowExecution, execution)
            run.status = "cancelled"
            await db.commit()
            await finish_resume_node(
                db,
                project,
                execution,
                receipt,
                token,
                status="cancelled",
                inputs={"capture_status": "redacted"},
                output=None,
                error=None,
                duration=0,
            )
            row = await db.get(NodeExecution, receipt)
            assert row.status == "cancelled" and row.output is None
            assert row.input == {"capture_status": "redacted"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("request_status", ["pending", "rejected", "expired"])
async def test_changed_decision_is_never_requeued(request_status):
    engine, sessions, project, execution, _, approval, _, _ = await claimed()
    try:
        async with sessions() as db:
            request = await db.get(WorkflowApprovalRequest, approval)
            request.status = request_status
            await db.commit()
            assert (
                await recover_interrupted_approval(
                    db,
                    project,
                    execution,
                    cutoff=datetime.now(UTC) + timedelta(seconds=1),
                )
                is None
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_sweep_observes_real_hard_limit_and_durable_state(monkeypatch):
    engine, sessions, project, execution, _, _, _, _ = await claimed()
    now = datetime.now(UTC)
    try:
        monkeypatch.setattr(approval_recovery_tasks, "workflow_task_session", sessions)
        monkeypatch.setattr(tasks.execute_workflow_task, "time_limit", 360)
        monkeypatch.setattr(celery_app.conf, "task_time_limit", 1)
        assert (
            await approval_recovery_tasks.sweep_interrupted_approval_resumes(now=now)
            == 0
        )
        assert (
            await approval_recovery_tasks.sweep_interrupted_approval_resumes(
                now=now + timedelta(seconds=481),
            )
            == 1
        )
        assert (
            await approval_recovery_tasks.sweep_interrupted_approval_resumes(
                now=now + timedelta(seconds=481),
            )
            == 0
        )
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            assert run.status == "pending_resume" and run.active_approval_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit", [None, False, 0, -1, float("inf"), float("nan"), 1e300]
)
async def test_unbounded_worker_limit_never_reclaims_a_live_owner(monkeypatch, limit):
    monkeypatch.setattr(tasks.execute_workflow_task, "time_limit", limit)
    monkeypatch.setattr(celery_app.conf, "task_time_limit", limit)
    with pytest.raises(RuntimeError, match="硬时限"):
        await approval_recovery_tasks.sweep_interrupted_approval_resumes()


def test_claim_recovery_has_an_independent_beat_entry():
    entry = celery_app.conf.beat_schedule["recover-interrupted-approval-resumes"]
    assert entry["task"] == "recover_interrupted_approval_resumes"
    assert entry["schedule"] == 60.0
    assert entry["options"]["queue"] == settings.WORKFLOW_QUEUE_NAME
    assert "celery_app.approval_recovery_tasks" in celery_app.conf.include


def test_recovery_task_retries_with_a_public_error_only(monkeypatch, caplog):
    sweep = AsyncMock(return_value=3)
    task = approval_recovery_tasks.recover_interrupted_approval_resumes_task
    monkeypatch.setattr(
        approval_recovery_tasks, "sweep_interrupted_approval_resumes", sweep
    )
    assert task.run() == 3
    sweep.side_effect = RuntimeError("private-fixture-error")
    retry = Mock(side_effect=RuntimeError("public-retry"))
    monkeypatch.setattr(task, "retry", retry)
    with pytest.raises(RuntimeError, match="public-retry"):
        task.run()
    assert "private-fixture-error" not in caplog.text
    assert "private-fixture-error" not in str(retry.call_args.kwargs["exc"])


@pytest.mark.asyncio
async def test_live_foreign_and_cancelled_claims_are_not_recovered():
    engine, sessions, project, execution, _, _, _, _ = await claimed()
    try:
        async with sessions() as db:
            assert (
                await recover_interrupted_approval(
                    db,
                    project,
                    execution,
                    cutoff=datetime.now(UTC) - timedelta(minutes=1),
                )
                is None
            )
            assert (
                await recover_interrupted_approval(
                    db,
                    str(uuid4()),
                    execution,
                    cutoff=datetime.now(UTC) + timedelta(seconds=1),
                )
                is None
            )
            run = await db.get(WorkflowExecution, execution)
            run.status = "cancelled"
            await db.commit()
            assert (
                await recover_interrupted_approval(
                    db,
                    project,
                    execution,
                    cutoff=datetime.now(UTC) + timedelta(seconds=1),
                )
                is None
            )
            await db.refresh(run)
            assert run.status == "cancelled"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_owner_cannot_begin_or_finish_after_a_new_claim():
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        old_token,
        _,
    ) = await claimed()
    try:
        async with sessions() as db:
            await recover_interrupted_approval(
                db,
                project,
                execution,
                cutoff=datetime.now(UTC) + timedelta(seconds=1),
            )
            run = await db.get(WorkflowExecution, execution)
            await claim_approved_resume(
                db,
                project,
                execution,
                run.workflow_id,
                expected_approval_id=approval,
                expected_approver_id=actor,
            )
            await db.refresh(run)
            token = run.active_segment_started_at
            with pytest.raises(ExecutionStopped):
                await begin_resume_node(
                    db, project, execution, "answer", "answer", old_token
                )
            assert not await finish_running_execution(
                db,
                execution,
                project,
                "completed",
                "stale",
                None,
                1,
                expected_segment_started_at=old_token,
            )
            attempt = await begin_resume_node(
                db, project, execution, "answer", "answer", token
            )
            await finish_resume_node(
                db,
                project,
                execution,
                attempt,
                token,
                status="completed",
                inputs={},
                output={"result": "original"},
                error=None,
                duration=1,
            )
            assert await finish_running_execution(
                db,
                execution,
                project,
                "completed",
                "original",
                None,
                1,
                expected_segment_started_at=token,
            )
            assert len((await db.scalars(select(NodeExecution))).all()) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_node_start_cannot_create_a_second_action_intent():
    engine, sessions, project, execution, _, _, token, _ = await claimed()
    try:
        async with sessions() as db:
            await begin_resume_node(db, project, execution, "business", "tool", token)
            with pytest.raises(ExecutionStopped):
                await begin_resume_node(
                    db, project, execution, "business", "tool", token
                )
            assert len((await db.scalars(select(NodeExecution))).all()) == 1
    finally:
        await engine.dispose()
