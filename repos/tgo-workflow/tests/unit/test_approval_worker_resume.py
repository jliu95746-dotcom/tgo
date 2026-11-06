"""A queued workflow must wait for approval and resume without replay."""

from datetime import UTC, datetime
import hashlib
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import executions
from app.models.approval import WorkflowApprovalRequest
from app.models.aftersales_review import WorkflowAfterSalesReview
from app.models.execution import NodeExecution, WorkflowExecution
from app.services.approval_state import decide_approval
from app.services.approval_preview import approval_preview_digest
from app.services.aftersales_review import review_preview
from celery_app import tasks


def approval_definition(approver_id: str):
    return {
        "nodes": [
            {"id": "start", "type": "input", "data": {
                "type": "input", "label": "输入", "reference_key": "input",
                "input_variables": [],
            }},
            {"id": "approval", "type": "approval", "data": {
                "type": "approval", "label": "确认", "reference_key": "approval",
                "approver_staff_id": approver_id,
                "summary": "核对售后登记", "expires_in_minutes": 60,
            }},
            {"id": "answer", "type": "answer", "data": {
                "type": "answer", "label": "答复", "reference_key": "answer",
                "output_type": "template", "output_template": "已确认",
            }},
        ],
        "edges": [
            {"source": "start", "target": "approval"},
            {"source": "approval", "target": "answer"},
        ],
    }


@pytest.mark.asyncio
async def test_worker_waits_and_resumes_exactly_once(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, workflow_id, approver_id = (str(uuid4()) for _ in range(3))
    inputs = {"order_number": "LY-123"}
    definition = approval_definition(approver_id)
    definition["nodes"][0]["data"]["input_variables"] = [
        {"name": "order_number", "type": "string", "required": True},
    ]
    definition["nodes"][1]["data"]["summary"] = "核对订单 {{input.order_number}}"
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        async with sessions() as db:
            await db.execute(
                text("INSERT INTO wf_workflows (id) VALUES (:workflow_id)"),
                {"workflow_id": workflow_id},
            )
            row = await executions._create_execution_record(
                db, workflow_id, project_id, inputs, status="pending",
                workflow_definition=definition,
                workflow_version=1,
            )
            execution_id = row.id

        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        verify_staff = AsyncMock()
        monkeypatch.setattr(tasks, "verify_approval_assignees", verify_staff)
        await tasks.async_execute_workflow(execution_id, workflow_id, inputs, project_id)
        async with sessions() as db:
            row = await db.get(WorkflowExecution, execution_id)
            requests = (await db.scalars(select(WorkflowApprovalRequest))).all()
            nodes = (await db.scalars(select(NodeExecution).where(
                NodeExecution.execution_id == execution_id,
            ))).all()
            assert row.status == "waiting_approval"
            assert row.completed_at is None
            assert len(requests) == 1
            assert requests[0].status == "pending"
            assert requests[0].summary == "核对订单 LY-123"
            assert requests[0].expires_at > datetime.now(UTC).replace(tzinfo=None)
            assert [node.node_id for node in nodes] == ["start", "approval"]
            approval_id = requests[0].id
            preview_digest = approval_preview_digest(
                approval_id, execution_id, requests[0].summary,
            )

        async with sessions() as db:
            await decide_approval(db, project_id, approval_id, approver_id, "approve",
                                  expected_preview_digest=preview_digest)
        await tasks.async_execute_workflow(execution_id, workflow_id, inputs, project_id)
        await tasks.async_execute_workflow(execution_id, workflow_id, inputs, project_id)
        async with sessions() as db:
            row = await db.get(WorkflowExecution, execution_id)
            nodes = (await db.scalars(select(NodeExecution).where(
                NodeExecution.execution_id == execution_id,
            ))).all()
            assert row.status == "completed"
            assert row.output == {"result": "已确认"}
            completed_approval = await db.get(WorkflowApprovalRequest, approval_id)
            assert completed_approval.checkpoint == {
                "capture_status": "redacted",
                "approval_preview_digest": preview_digest,
            }
            assert [node.node_id for node in nodes] == ["start", "approval", "answer"]
            assert verify_staff.await_count == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question", ["包的拉链坏了，如何处理？", "包的拉链坏了，如何处理？" * 42],
    ids=["short", "long"],
)
async def test_worker_creates_one_review_only_after_approval(monkeypatch, question):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, workflow_id, approver_id, visitor_session_id, employee_id, business_id = (
        str(uuid4()) for _ in range(6)
    )
    definition = approval_definition(approver_id)
    definition["nodes"][0]["data"]["input_variables"] = [
        {"name": "question", "type": "string", "required": True},
    ]
    definition["nodes"][1]["data"].update({
        "summary": "请核对客户售后诉求：{{input.question}}",
        "after_approval_action": "create_aftersales_review",
        "case_category": "return_request",
    })
    inputs = {"question": question}
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
            await connection.run_sync(WorkflowAfterSalesReview.__table__.create)
        async with sessions() as db:
            await db.execute(
                text("INSERT INTO wf_workflows (id) VALUES (:id)"),
                {"id": workflow_id},
            )
            row = await executions._create_execution_record(
                db, workflow_id, project_id, inputs, status="pending",
                workflow_definition=definition, workflow_version=1,
            )
            execution_id = row.id
            row.visitor_session_id = visitor_session_id
            row.employee_agent_id = employee_id
            row.business_request_id = business_id
            row.execution_mode = "production"
            row.release_id = str(uuid4())
            await db.commit()
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        monkeypatch.setattr(tasks, "verify_approval_assignees", AsyncMock())
        monkeypatch.setattr(tasks, "check_employee_binding", AsyncMock())
        await tasks.async_execute_workflow(execution_id, workflow_id, inputs, project_id)
        async with sessions() as db:
            request = (await db.scalars(select(WorkflowApprovalRequest))).one()
            assert request.summary.startswith("请核对客户售后诉求：")
            assert len(request.summary) <= 500
            if len(question) > 500:
                assert "已截取" in request.summary
                assert hashlib.sha256(question.encode("utf-8")).hexdigest() in request.summary
                assert question not in request.summary
            else:
                assert request.summary == review_preview(
                    f"请核对客户售后诉求：{question}", "return_request",
                    visitor_session_id,
                )
            assert (await db.scalars(select(WorkflowAfterSalesReview))).all() == []
            await decide_approval(
                db, project_id, request.id, approver_id, "approve",
                expected_preview_digest=approval_preview_digest(
                    request.id, execution_id, request.summary,
                ),
            )
        await tasks.async_execute_workflow(execution_id, workflow_id, inputs, project_id)
        await tasks.async_execute_workflow(execution_id, workflow_id, inputs, project_id)
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution_id)
            reviews = (await db.scalars(select(WorkflowAfterSalesReview))).all()
            assert run.status == "completed"
            assert len(reviews) == 1
            assert reviews[0].approval_id == request.id
            assert reviews[0].visitor_session_id == visitor_session_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_does_not_resume_after_approver_revocation(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, workflow_id, approver_id = (str(uuid4()) for _ in range(3))
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        async with sessions() as db:
            await db.execute(
                text("INSERT INTO wf_workflows (id) VALUES (:id)"),
                {"id": workflow_id},
            )
            row = await executions._create_execution_record(
                db, workflow_id, project_id, {}, status="pending",
                workflow_definition=approval_definition(approver_id), workflow_version=1,
            )
            execution_id = row.id
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        verify_staff = AsyncMock()
        monkeypatch.setattr(tasks, "verify_approval_assignees", verify_staff)
        await tasks.async_execute_workflow(execution_id, workflow_id, {}, project_id)
        async with sessions() as db:
            approval = (await db.scalars(select(WorkflowApprovalRequest))).one()
            await decide_approval(
                db, project_id, approval.id, approver_id, "approve",
                expected_preview_digest=approval_preview_digest(
                    approval.id, execution_id, approval.summary,
                ),
            )
        verify_staff.side_effect = RuntimeError("审批人核验服务不可用")
        with pytest.raises(RuntimeError):
            await tasks.async_execute_workflow(execution_id, workflow_id, {}, project_id)
        async with sessions() as db:
            row = await db.get(WorkflowExecution, execution_id)
            assert row.status == "pending_resume"
        verify_staff.side_effect = PermissionError("审批员工已撤权")
        await tasks.async_execute_workflow(execution_id, workflow_id, {}, project_id)
        async with sessions() as db:
            row = await db.get(WorkflowExecution, execution_id)
            nodes = (await db.scalars(select(NodeExecution).where(
                NodeExecution.execution_id == execution_id,
            ))).all()
            assert row.status == "failed"
            assert "权限" in row.error
            assert [node.node_id for node in nodes] == ["start", "approval"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke_call", [2, 3])
async def test_worker_rechecks_approver_after_claim(
    monkeypatch, revoke_call,
):
    """Revocation before the node or its receipt stops the resumed run."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, workflow_id, approver_id = (str(uuid4()) for _ in range(3))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)")
            )
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        async with sessions() as db:
            await db.execute(
                text("INSERT INTO wf_workflows (id) VALUES (:id)"),
                {"id": workflow_id},
            )
            row = await executions._create_execution_record(
                db, workflow_id, project_id, {}, status="pending",
                workflow_definition=approval_definition(approver_id),
                workflow_version=1,
            )
            execution_id = row.id
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        verify_staff = AsyncMock()
        monkeypatch.setattr(tasks, "verify_approval_assignees", verify_staff)
        await tasks.async_execute_workflow(
            execution_id, workflow_id, {}, project_id,
        )
        async with sessions() as db:
            approval = (await db.scalars(select(WorkflowApprovalRequest))).one()
            await decide_approval(
                db, project_id, approval.id, approver_id, "approve",
                expected_preview_digest=approval_preview_digest(
                    approval.id, execution_id, approval.summary,
                ),
            )
        verify_staff.reset_mock()
        verify_staff.side_effect = [None] * (revoke_call - 1) + [
            PermissionError("审批员工已撤权"),
        ]
        await tasks.async_execute_workflow(
            execution_id, workflow_id, {}, project_id,
        )
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution_id)
            nodes = (await db.scalars(select(NodeExecution).where(
                NodeExecution.execution_id == execution_id,
            ))).all()
            assert run.status == "failed"
            assert run.output is None
            assert [node.node_id for node in nodes] == (
                ["start", "approval", "answer"] if revoke_call == 3
                else ["start", "approval"]
            )
            if revoke_call == 3:
                assert nodes[-1].status == "running"
                assert nodes[-1].input is None and nodes[-1].output is None
        assert verify_staff.await_count == revoke_call
    finally:
        await engine.dispose()
