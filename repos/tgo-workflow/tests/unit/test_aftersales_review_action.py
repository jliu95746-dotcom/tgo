"""An approval authorizes exactly one internal aftersales review task."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.engine.checkpoint import ExecutionCheckpoint
from app.models.approval import WorkflowApprovalRequest
from app.models.aftersales_review import WorkflowAfterSalesReview
from app.models.execution import WorkflowExecution
from app.services.aftersales_review import (
    AfterSalesReviewConflict, create_approved_review, list_assigned_reviews,
    render_review_approval_preview, review_preview,
)
from app.services.execution_snapshot import definition_digest
from app.services.approval_preview import approval_preview_digest
from app.services.approval_checkpoint import seal_checkpoint
from app.services.workflow_service import WorkflowService


def review_definition(category: str = "return_request") -> dict:
    return {"nodes": [{"id": "approval", "type": "approval", "data": {
        "summary": "核对客户诉求", "after_approval_action": "create_aftersales_review",
        "case_category": category,
    }}], "edges": []}


def saved_checkpoint(project: str, execution: str, workflow: str, definition: dict) -> dict:
    return ExecutionCheckpoint(
        project_id=project, execution_id=execution,
        definition_digest=definition_digest(definition),
        input_digest=definition_digest({}), workflow_lineage=[workflow],
        context_data={}, current_nodes=[], next_nodes=[],
        completed_nodes=["approval"], node_index=2,
    ).model_dump(mode="json")


def test_long_aftersales_preview_binds_the_full_request_and_keeps_other_templates_strict():
    session_id = str(uuid4())
    template = "请核对客户售后诉求：{{input.question}}"
    first_question = "拉链损坏。" * 100 + "希望换货"
    second_question = "拉链损坏。" * 100 + "希望退货"
    first = render_review_approval_preview(
        template, {"input.question": first_question}, "exchange_request", session_id,
    )
    second = render_review_approval_preview(
        template, {"input.question": second_question}, "exchange_request", session_id,
    )
    assert len(first) <= 500
    assert first != second
    assert "已截取" in first and "客户会话" in first
    assert first_question not in first
    with pytest.raises(ValueError, match="过长"):
        render_review_approval_preview(
            "请核对：{{input.reason}}", {"input.reason": first_question},
            "exchange_request", session_id,
        )
    with pytest.raises(ValueError, match="未解析"):
        render_review_approval_preview(
            template, {"input.question": "{{bad}}"}, "exchange_request", session_id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["legacy", "encrypted"])
async def test_approved_review_is_idempotent_and_bound_to_visible_case(monkeypatch, storage):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, workflow, execution, approval, session, approver, employee, business = (
        str(uuid4()) for _ in range(8)
    )
    definition = review_definition()
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
        await connection.run_sync(WorkflowExecution.__table__.create)
        await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        await connection.run_sync(WorkflowAfterSalesReview.__table__.create)
    try:
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES (:id)"), {"id": workflow})
            db.add(WorkflowExecution(
                id=execution, project_id=project, workflow_id=workflow,
                status="running", visitor_session_id=session,
                execution_mode="production", employee_agent_id=employee,
                business_request_id=business,
                definition_snapshot=definition, definition_digest=definition_digest(definition),
            ))
            db.add(WorkflowApprovalRequest(
                id=approval, project_id=project, execution_id=execution,
                node_id="approval", approver_staff_id=approver,
                summary=review_preview("核对客户诉求", "return_request", session),
                checkpoint=saved_checkpoint(project, execution, workflow, definition),
                status="approved", decided_by=approver, decided_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ))
            await db.commit()
            if storage == "encrypted":
                run = await db.get(WorkflowExecution, execution)
                request = await db.get(WorkflowApprovalRequest, approval)
                request.checkpoint = seal_checkpoint(
                    run, request, ExecutionCheckpoint.model_validate(request.checkpoint),
                )
                await db.commit()
        async with sessions() as db:
            first = await create_approved_review(db, project, execution, approval)
            repeated = await create_approved_review(db, project, execution, approval)
            assert first.id == repeated.id
            assert first.category == "return_request"
            assert first.visitor_session_id == session
            assert first.status == "pending_staff_review"
            assert len((await db.scalars(select(WorkflowAfterSalesReview))).all()) == 1
            run = await db.get(WorkflowExecution, execution)
            run.status = "completed"
            run.input = None
            saved_approval = await db.get(WorkflowApprovalRequest, approval)
            saved_approval.checkpoint = {
                "capture_status": "redacted",
                "approval_preview_digest": approval_preview_digest(
                    approval, execution, saved_approval.summary,
                ),
            }
            await db.commit()
            assert (await create_approved_review(db, project, execution, approval)).id == first.id
            run.status = "running"
            await db.commit()
            assigned = await list_assigned_reviews(db, project, approver)
            assert [item.id for item in assigned] == [first.id]
            assert await list_assigned_reviews(db, project, str(uuid4())) == []
            assert await list_assigned_reviews(db, str(uuid4()), approver) == []

            async def existing_workflow(*_args):
                return object()

            monkeypatch.setattr(WorkflowService, "get_by_id", existing_workflow)
            with pytest.raises(HTTPException) as blocked:
                await WorkflowService.delete(db, workflow, project)
            assert blocked.value.status_code == 409
            assert await db.get(WorkflowExecution, execution) is not None
            with pytest.raises(AfterSalesReviewConflict):
                await create_approved_review(db, str(uuid4()), execution, approval)
            with pytest.raises(AfterSalesReviewConflict):
                await create_approved_review(db, project, execution, str(uuid4()))
        async with sessions() as db:
            request = await db.get(WorkflowApprovalRequest, approval)
            request.summary = "核对客户诉求"
            await db.commit()
        async with sessions() as db:
            with pytest.raises(AfterSalesReviewConflict):
                await create_approved_review(db, project, execution, approval)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unapproved_or_changed_action_cannot_create_review():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, workflow, execution, approval, session, approver, employee, business = (
        str(uuid4()) for _ in range(8)
    )
    definition = review_definition()
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
        await connection.run_sync(WorkflowExecution.__table__.create)
        await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        await connection.run_sync(WorkflowAfterSalesReview.__table__.create)
    try:
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES (:id)"), {"id": workflow})
            db.add(WorkflowExecution(
                id=execution, project_id=project, workflow_id=workflow,
                status="running", visitor_session_id=session,
                execution_mode="production", employee_agent_id=employee,
                business_request_id=business,
                definition_snapshot=definition, definition_digest=definition_digest(definition),
            ))
            db.add(WorkflowApprovalRequest(
                id=approval, project_id=project, execution_id=execution,
                node_id="approval", approver_staff_id=approver,
                summary=review_preview("核对客户诉求", "return_request", session),
                checkpoint=saved_checkpoint(project, execution, workflow, definition),
                status="pending",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ))
            await db.commit()
        async with sessions() as db:
            with pytest.raises(AfterSalesReviewConflict):
                await create_approved_review(db, project, execution, approval)
            assert (await db.scalars(select(WorkflowAfterSalesReview))).all() == []
        async with sessions() as db:
            request = await db.get(WorkflowApprovalRequest, approval)
            request.status = "approved"
            request.decided_by = str(uuid4())
            request.decided_at = datetime.now(UTC)
            await db.commit()
        async with sessions() as db:
            with pytest.raises(AfterSalesReviewConflict):
                await create_approved_review(db, project, execution, approval)
            request = await db.get(WorkflowApprovalRequest, approval)
            request.decided_by = approver
            request.checkpoint = {}
            await db.commit()
        async with sessions() as db:
            with pytest.raises(AfterSalesReviewConflict):
                await create_approved_review(db, project, execution, approval)
            request = await db.get(WorkflowApprovalRequest, approval)
            request.checkpoint = saved_checkpoint(project, execution, workflow, definition)
            run = await db.get(WorkflowExecution, execution)
            run.visitor_session_id = str(uuid4())
            await db.commit()
        async with sessions() as db:
            with pytest.raises(AfterSalesReviewConflict):
                await create_approved_review(db, project, execution, approval)
            assert (await db.scalars(select(WorkflowAfterSalesReview))).all() == []
    finally:
        await engine.dispose()
