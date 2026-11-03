"""Approval state and its checkpoint change atomically within one project."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.engine.checkpoint import ExecutionCheckpoint
from app.api import executions
from app.models.execution import WorkflowExecution
from app.models.approval import WorkflowApprovalRequest
from app.services.approval_state import (
    ApprovalConflict, approved_resume_assignee, claim_approved_resume,
    decide_approval,
    fail_revoked_approved_resume, expire_approval, pause_for_approval,
)
from app.services.execution_snapshot import definition_digest
from app.services.approval_preview import approval_preview_digest
from app.services.approval_checkpoint import open_checkpoint
from celery_app import tasks


def preview_for(request: WorkflowApprovalRequest) -> str:
    return approval_preview_digest(request.id, request.execution_id, request.summary)


async def prepared_run(summary_template: str = "确认"):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, execution_id = str(uuid4()), str(uuid4())
    workflow_id, approver_id = str(uuid4()), str(uuid4())
    definition = {"nodes": [{"id": "approval", "type": "approval", "data": {
        "summary": summary_template,
    }}], "edges": []}
    inputs = {"order": "ORDER-1"}
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
        await connection.run_sync(WorkflowExecution.__table__.create)
        await connection.run_sync(WorkflowApprovalRequest.__table__.create)
    async with sessions() as db:
        await db.execute(
            text("INSERT INTO wf_workflows (id) VALUES (:workflow_id)"),
            {"workflow_id": workflow_id},
        )
        db.add(WorkflowExecution(
            id=execution_id, project_id=project_id, workflow_id=workflow_id,
            status="running", input=inputs, definition_snapshot=definition,
            definition_digest=definition_digest(definition),
            workflow_lineage=[workflow_id], workflow_version=1,
        ))
        await db.commit()
    checkpoint = ExecutionCheckpoint(
        project_id=project_id, execution_id=execution_id,
        definition_digest=definition_digest(definition),
        input_digest=definition_digest(inputs),
        workflow_lineage=[workflow_id], context_data={"input.order": "ORDER-1"},
        current_nodes=[], next_nodes=[], completed_nodes=["approval"],
        node_index=2,
    )
    return engine, sessions, project_id, execution_id, approver_id, checkpoint


@pytest.mark.asyncio
async def test_approval_wait_and_decision_are_project_scoped_and_single_use():
    engine, sessions, project, execution, approver, checkpoint = await prepared_run(
        "确认登记售后申请",
    )
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db, project, execution, "approval", approver,
                "确认登记售后申请", checkpoint,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            row = await db.get(WorkflowExecution, execution)
            assert row.status == "waiting_approval"
            assert row.active_approval_id == request.id
            assert open_checkpoint(row, request).execution_id == execution
        async with sessions() as db:
            with pytest.raises(ApprovalConflict):
                await decide_approval(db, str(uuid4()), request.id, approver, "approve",
                                      expected_preview_digest=preview_for(request))
            with pytest.raises(ApprovalConflict):
                await decide_approval(db, project, request.id, str(uuid4()), "approve",
                                      expected_preview_digest=preview_for(request))
            approved = await decide_approval(db, project, request.id, approver, "approve",
                                             expected_preview_digest=preview_for(request))
            assert approved.status == "approved"
            row = await db.get(WorkflowExecution, execution)
            assert row.status == "pending_resume"
            assert open_checkpoint(row, request).context_data["input.order"] == "ORDER-1"
        async with sessions() as db:
            with pytest.raises(ApprovalConflict):
                await decide_approval(db, project, request.id, approver, "reject",
                                      expected_preview_digest=preview_for(request))
            approvals = (await db.scalars(select(WorkflowApprovalRequest))).all()
            assert len(approvals) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approval_requires_the_visible_preview_digest():
    engine, sessions, project, execution, approver, checkpoint = await prepared_run(
        "订单 {{input.order}} 售后登记",
    )
    try:
        async with sessions() as db:
            with pytest.raises(ApprovalConflict, match="审批预览"):
                await pause_for_approval(
                    db, project, execution, "approval", approver,
                    "订单 LY-123 售后登记", checkpoint,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            request = await pause_for_approval(
                db, project, execution, "approval", approver,
                "订单 ORDER-1 售后登记", checkpoint,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            assert request.summary == "订单 ORDER-1 售后登记"
        async with sessions() as db:
            with pytest.raises(ApprovalConflict):
                await decide_approval(
                    db, project, request.id, approver, "approve",
                    expected_preview_digest="0" * 64,
                )
            assert (await db.get(WorkflowExecution, execution)).status == "waiting_approval"
            assert (await db.get(WorkflowApprovalRequest, request.id)).status == "pending"
            digest = approval_preview_digest(
                request.id, execution, "订单 ORDER-1 售后登记",
            )
            approved = await decide_approval(
                db, project, request.id, approver, "approve",
                expected_preview_digest=digest,
            )
            assert approved.status == "approved"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revoked_approver_stops_only_the_original_pending_resume():
    engine, sessions, project, execution, approver, checkpoint = await prepared_run(
        "核对只读结果",
    )
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db, project, execution, "approval", approver, "核对只读结果", checkpoint,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        async with sessions() as db:
            await decide_approval(db, project, request.id, approver, "approve",
                                  expected_preview_digest=preview_for(request))
            assert await approved_resume_assignee(db, project, execution, checkpoint.workflow_lineage[0]) == (request.id, approver)
            assert not await fail_revoked_approved_resume(db, str(uuid4()), execution, request.id)
            assert await fail_revoked_approved_resume(db, project, execution, request.id)
            row = await db.get(WorkflowExecution, execution)
            assert row.status == "failed"
            assert row.input is None
            saved_request = await db.get(WorkflowApprovalRequest, request.id)
            await db.refresh(saved_request)
            assert saved_request.checkpoint == {
                "capture_status": "redacted",
                "approval_preview_digest": preview_for(saved_request),
            }
            assert "权限" in row.error
            assert not await fail_revoked_approved_resume(db, project, execution, request.id)
            assert await approved_resume_assignee(db, project, execution, checkpoint.workflow_lineage[0]) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resume_claim_cannot_switch_the_verified_approval():
    engine, sessions, project, execution, approver, checkpoint = (
        await prepared_run("核对只读结果")
    )
    workflow_id = checkpoint.workflow_lineage[0]
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db, project, execution, "approval", approver,
                "核对只读结果", checkpoint,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            await decide_approval(
                db, project, request.id, approver, "approve",
                expected_preview_digest=preview_for(request),
            )
        async with sessions() as db:
            assert await claim_approved_resume(
                db, project, execution, workflow_id,
                expected_approval_id=str(uuid4()),
                expected_approver_id=approver,
            ) is None
            assert (await db.get(WorkflowExecution, execution)).status == "pending_resume"
            saved = await db.get(WorkflowApprovalRequest, request.id)
            saved.approver_staff_id = str(uuid4())
            await db.commit()
        async with sessions() as db:
            assert await claim_approved_resume(
                db, project, execution, workflow_id,
                expected_approval_id=request.id,
                expected_approver_id=approver,
            ) is None
            assert (await db.get(WorkflowExecution, execution)).status == "pending_resume"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_or_modified_run_cannot_create_or_approve_wait():
    engine, sessions, project, execution, approver, checkpoint = await prepared_run()
    try:
        async with sessions() as db:
            with pytest.raises(ApprovalConflict):
                await pause_for_approval(
                    db, project, execution, "approval", approver, "确认",
                    checkpoint.model_copy(update={"input_digest": "wrong"}),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            with pytest.raises(ApprovalConflict):
                await pause_for_approval(
                    db, project, execution, "approval", approver, "确认",
                    checkpoint, expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            request = await pause_for_approval(
                db, project, execution, "approval", approver, "确认",
                checkpoint, expires_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        async with sessions() as db:
            with pytest.raises(ApprovalConflict):
                await decide_approval(
                    db, project, request.id, approver, "approve",
                    expected_preview_digest=preview_for(request),
                    now=datetime.now(UTC) + timedelta(seconds=2),
                )
            row = await db.get(WorkflowExecution, execution)
            assert row.status == "waiting_approval"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rejection_finishes_the_run_without_resuming():
    engine, sessions, project, execution, approver, checkpoint = await prepared_run()
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db, project, execution, "approval", approver, "确认",
                checkpoint, expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        async with sessions() as db:
            rejected = await decide_approval(
                db, project, request.id, approver, "reject",
                expected_preview_digest=preview_for(request),
            )
            assert rejected.status == "rejected"
            row = await db.get(WorkflowExecution, execution)
            assert row.status == "rejected"
            assert row.input is None
            assert rejected.checkpoint == {
                "capture_status": "redacted",
                "approval_preview_digest": preview_for(rejected),
            }
            assert row.completed_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expiry_competes_with_confirmation_and_finishes_once():
    engine, sessions, project, execution, approver, checkpoint = await prepared_run()
    try:
        deadline = datetime.now(UTC) + timedelta(seconds=1)
        async with sessions() as db:
            request = await pause_for_approval(
                db, project, execution, "approval", approver, "确认",
                checkpoint, expires_at=deadline,
            )
        async with sessions() as db:
            assert not await expire_approval(
                db, project, request.id, now=deadline - timedelta(seconds=1),
            )
            assert not await expire_approval(
                db, str(uuid4()), request.id, now=deadline + timedelta(seconds=1),
            )
            assert await expire_approval(
                db, project, request.id, now=deadline + timedelta(seconds=1),
            )
            assert not await expire_approval(
                db, project, request.id, now=deadline + timedelta(seconds=2),
            )
            row = await db.get(WorkflowExecution, execution)
            assert row.status == "expired"
            assert row.input is None
            saved_request = await db.get(WorkflowApprovalRequest, request.id)
            assert saved_request.checkpoint == {
                "capture_status": "redacted",
                "approval_preview_digest": preview_for(saved_request),
            }
            with pytest.raises(ApprovalConflict):
                await decide_approval(
                    db, project, request.id, approver, "approve",
                    expected_preview_digest=preview_for(request),
                    now=deadline + timedelta(seconds=2),
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_cancel_stops_waiting_or_pending_resume(monkeypatch, approved):
    engine, sessions, project, execution, approver, checkpoint = await prepared_run()
    monkeypatch.setattr(executions, "revoke_queued_execution", AsyncMock())
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db, project, execution, "approval", approver, "确认",
                checkpoint, expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        if approved:
            async with sessions() as db:
                await decide_approval(db, project, request.id, approver, "approve",
                                      expected_preview_digest=preview_for(request))
        async with sessions() as db:
            result = await executions.cancel_execution(execution, project, db)
            assert result.status == "cancelled"
        async with sessions() as db:
            row = await db.get(WorkflowExecution, execution)
            request = await db.get(WorkflowApprovalRequest, request.id)
            assert row.status == "cancelled"
            assert row.input is None
            assert request.status == ("approved" if approved else "cancelled")
            assert request.checkpoint == {
                "capture_status": "redacted",
                "approval_preview_digest": preview_for(request),
            }
            with pytest.raises(ApprovalConflict):
                await decide_approval(db, project, request.id, approver, "approve",
                                      expected_preview_digest=preview_for(request))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expiry_sweep_finishes_wait_without_employee_request(monkeypatch):
    engine, sessions, project, execution, approver, checkpoint = await prepared_run()
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db, project, execution, "approval", approver, "确认",
                checkpoint, expires_at=deadline,
            )
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        assert await tasks.sweep_expired_approvals(now=deadline + timedelta(seconds=1)) == 1
        assert await tasks.sweep_expired_approvals(now=deadline + timedelta(seconds=2)) == 0
        async with sessions() as db:
            assert (await db.get(WorkflowExecution, execution)).status == "expired"
            assert (await db.get(WorkflowApprovalRequest, request.id)).status == "expired"
    finally:
        await engine.dispose()
