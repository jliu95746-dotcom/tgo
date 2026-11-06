"""Staff resolve the exact interrupted run without replaying a saved step."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.config import settings
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import NodeExecution, WorkflowExecution
from app.services.approval_preview import approval_preview_digest
from app.services.approval_reconciliation import (
    ReconciliationConflict,
    decide_reconciliation,
    get_reconciliation,
    list_reconciliations,
    get_reconciliation_decision,
)
from app.services.approval_resume_progress import save_resume_progress
from app.services.approval_checkpoint import VERIFIED_RESUME_CAPTURE
from app.services.approval_state import ApprovalConflict
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
from app.services.execution_snapshot import definition_digest
from celery_app import tasks
from tests.unit.test_approval_state import prepared_run
from tests.unit.test_approval_worker_resume import approval_definition


@pytest.fixture(autouse=True)
def reconciliation_key(monkeypatch):
    monkeypatch.setattr(
        settings, "SECRET_KEY", SecretStr("test-reconciliation-" + "a" * 48)
    )


async def interrupted(*, completed: bool):
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        checkpoint,
    ) = await prepared_run()
    async with engine.begin() as connection:
        await connection.run_sync(NodeExecution.__table__.create)
    async with sessions() as db:
        run = await db.get(WorkflowExecution, execution)
        graph = approval_definition(actor)
        graph["nodes"][0]["data"]["input_variables"] = [
            {"name": "order", "type": "string", "required": True},
        ]
        run.definition_snapshot = graph
        run.definition_digest = definition_digest(graph)
        checkpoint = checkpoint.model_copy(
            update={
                "definition_digest": run.definition_digest,
                "completed_nodes": ["start", "approval"],
                "next_nodes": ["answer"],
                "node_index": 3,
            }
        )
        await db.commit()
        request = await pause_for_approval(
            db,
            project,
            execution,
            "approval",
            actor,
            "核对售后登记",
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
        await claim_approved_resume(
            db,
            project,
            execution,
            run.workflow_id,
            expected_approval_id=request.id,
            expected_approver_id=actor,
        )
        await db.refresh(run)
        token = run.active_segment_started_at
        receipt = await begin_resume_node(
            db,
            project,
            execution,
            "answer",
            "answer",
            token,
        )
        if completed:
            await finish_resume_node(
                db,
                project,
                execution,
                receipt,
                token,
                status="completed",
                inputs={},
                output={"result": "已确认"},
                error=None,
                duration=1,
                commit=False,
            )
            saved = checkpoint.model_copy(
                update={
                    "completed_nodes": ["start", "approval", "answer"],
                    "next_nodes": [],
                    "node_index": 4,
                    "final_output": "已确认",
                    "context_data": {
                        "input.order": "ORDER-1",
                        "answer.result": "已确认",
                    },
                }
            )
            await save_resume_progress(
                db,
                project,
                execution,
                request.id,
                token,
                saved,
            )
        assert (
            await recover_interrupted_approval(
                db,
                project,
                execution,
                cutoff=datetime.now(UTC) + timedelta(seconds=1),
            )
            == "reconciliation_required"
        )
        return engine, sessions, project, execution, actor, request.id, receipt


@pytest.mark.asyncio
async def test_saved_result_continues_without_reexecuting_completed_node(
    monkeypatch,
):
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        receipt,
    ) = await interrupted(completed=True)
    try:
        async with sessions() as db:
            review = await get_reconciliation(db, project, actor, approval)
            assert (
                review.can_continue and review.nodes[0].status == "completed"
            )
            decision = await decide_reconciliation(
                db,
                project,
                actor,
                approval,
                "continue",
                review.review_digest,
            )
            assert decision.status == "pending_resume"
            run = await db.get(WorkflowExecution, execution)
            workflow = run.workflow_id
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        monkeypatch.setattr(tasks, "verify_approval_assignees", AsyncMock())
        await tasks.async_execute_workflow(execution, workflow, {}, project)
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            assert run.status == "completed" and run.output == {
                "result": "已确认"
            }
            rows = list((await db.scalars(select(NodeExecution))).all())
            assert len(rows) == 1 and rows[0].id == receipt
            request = await db.get(WorkflowApprovalRequest, approval)
            assert request.resume_checkpoint is None
            assert (
                request.reconciliation_ledger["decisions"][0]["decision"]
                == "continue"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_action_cannot_be_claimed_complete_and_can_be_stopped():
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        _,
    ) = await interrupted(completed=False)
    try:
        async with sessions() as db:
            review = await get_reconciliation(db, project, actor, approval)
            assert (
                not review.can_continue and review.nodes[0].status == "running"
            )
            with pytest.raises(ReconciliationConflict):
                await decide_reconciliation(
                    db,
                    project,
                    actor,
                    approval,
                    "continue",
                    review.review_digest,
                )
            result = await decide_reconciliation(
                db,
                project,
                actor,
                approval,
                "stop",
                review.review_digest,
            )
            assert result.status == "cancelled"
            request = await db.get(WorkflowApprovalRequest, approval)
            assert request.checkpoint["capture_status"] == "redacted"
            assert request.resume_checkpoint is None
            repeated = await decide_reconciliation(
                db,
                project,
                actor,
                approval,
                "stop",
                review.review_digest,
            )
            assert repeated == result
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_scope_and_preview_are_checked():
    engine, sessions, project, _, actor, approval, _ = await interrupted(
        completed=True
    )
    try:
        async with sessions() as db:
            assert await list_reconciliations(db, str(uuid4()), actor) == []
            assert await list_reconciliations(db, project, str(uuid4())) == []
            for owner, staff, digest in [
                (str(uuid4()), actor, "0" * 64),
                (project, str(uuid4()), "0" * 64),
                (project, actor, "0" * 64),
            ]:
                with pytest.raises(ReconciliationConflict):
                    await decide_reconciliation(
                        db, owner, staff, approval, "stop", digest
                    )
            rows = await list_reconciliations(db, project, actor)
            assert len(rows) == 1 and rows[0].can_continue
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        "output",
        "status",
        "input",
        "ciphertext",
        "key",
        "missing",
        "approval-envelope",
    ],
)
async def test_changed_results_or_progress_block_continuation(changed):
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        receipt,
    ) = await interrupted(completed=True)
    try:
        async with sessions() as db:
            before = await get_reconciliation(db, project, actor, approval)
            request = await db.get(WorkflowApprovalRequest, approval)
            row = await db.get(NodeExecution, receipt)
            if changed == "output":
                row.output = {"result": "different-synthetic-result"}
            elif changed == "status":
                row.status = "running"
            elif changed == "input":
                run = await db.get(WorkflowExecution, execution)
                run.input = {"order": "OTHER-SYNTHETIC-ORDER"}
            elif changed == "missing":
                request.resume_checkpoint = None
            elif changed == "approval-envelope":
                request.resume_checkpoint = dict(request.checkpoint)
            else:
                envelope = dict(request.resume_checkpoint)
                envelope[
                    "ciphertext" if changed == "ciphertext" else "key_id"
                ] = "invalid"
                request.resume_checkpoint = envelope
            await db.commit()
            with pytest.raises(ReconciliationConflict):
                await decide_reconciliation(
                    db,
                    project,
                    actor,
                    approval,
                    "continue",
                    before.review_digest,
                )
            current = await get_reconciliation(db, project, actor, approval)
            assert (
                not current.can_continue
                and current.review_digest != before.review_digest
            )
            with pytest.raises(ReconciliationConflict):
                await decide_reconciliation(
                    db,
                    project,
                    actor,
                    approval,
                    "continue",
                    current.review_digest,
                )
            assert (
                await decide_reconciliation(
                    db, project, actor, approval, "stop", current.review_digest
                )
            ).status == "cancelled"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_decision_receipt_survives_response_loss_without_a_second_decision():
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        _,
    ) = await interrupted(completed=True)
    try:
        async with sessions() as db:
            view = await get_reconciliation(db, project, actor, approval)
            assert (
                await get_reconciliation_decision(
                    db, project, actor, approval, view.review_digest
                )
                is None
            )
            expected = await decide_reconciliation(
                db, project, actor, approval, "continue", view.review_digest
            )
        async with sessions() as db:
            assert (
                await get_reconciliation_decision(
                    db, project, actor, approval, view.review_digest
                )
                == expected
            )
            repeated = await decide_reconciliation(
                db, project, actor, approval, "continue", view.review_digest
            )
            assert repeated == expected
            with pytest.raises(ReconciliationConflict):
                await decide_reconciliation(
                    db, project, actor, approval, "stop", view.review_digest
                )
            request = await db.get(WorkflowApprovalRequest, approval)
            assert request.status == "approved" and request.decided_by == actor
            assert len(request.reconciliation_ledger["decisions"]) == 1
            assert "ORDER-1" not in str(request.reconciliation_ledger)
            assert "ciphertext" not in view.model_dump()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_extra_started_action_invalidates_the_saved_cursor():
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        _,
    ) = await interrupted(completed=True)
    try:
        async with sessions() as db:
            db.add(
                NodeExecution(
                    project_id=project,
                    execution_id=execution,
                    node_id="unexpected",
                    node_type="tool",
                    status="running",
                    started_at=datetime.now(UTC),
                )
            )
            await db.commit()
            view = await get_reconciliation(db, project, actor, approval)
            assert not view.can_continue
            with pytest.raises(ReconciliationConflict):
                await decide_reconciliation(
                    db,
                    project,
                    actor,
                    approval,
                    "continue",
                    view.review_digest,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_verified_continuation_keeps_ciphertext_and_refuses_missing_progress():
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        _,
    ) = await interrupted(completed=True)
    try:
        async with sessions() as db:
            request = await db.get(WorkflowApprovalRequest, approval)
            original = dict(request.checkpoint)
            review = await get_reconciliation(db, project, actor, approval)
            await decide_reconciliation(
                db, project, actor, approval, "continue", review.review_digest
            )
            assert (
                request.checkpoint["capture_status"] == VERIFIED_RESUME_CAPTURE
            )
            assert request.checkpoint["ciphertext"] == original["ciphertext"]
            assert request.checkpoint["key_id"] == original["key_id"]
            run = await db.get(WorkflowExecution, execution)
            workflow = run.workflow_id
            request.resume_checkpoint = None
            await db.commit()
            with pytest.raises(ApprovalConflict):
                await claim_approved_resume(
                    db,
                    project,
                    execution,
                    workflow,
                    expected_approval_id=approval,
                    expected_approver_id=actor,
                )
            await db.refresh(run)
            assert run.status == "pending_resume"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_incomplete_page_cursor_and_malformed_ledger_keep_original_records():
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        _,
    ) = await interrupted(completed=True)
    try:
        async with sessions() as db:
            for cursor in [
                dict(before_id=approval),
                dict(before_requested_at=datetime.now(UTC)),
            ]:
                with pytest.raises(ReconciliationConflict):
                    await list_reconciliations(db, project, actor, **cursor)
            request = await db.get(WorkflowApprovalRequest, approval)
            original = dict(request.checkpoint)
            request.reconciliation_ledger = {"version": 99}
            await db.commit()
            with pytest.raises(ReconciliationConflict):
                await get_reconciliation(db, project, actor, approval)
            with pytest.raises(ReconciliationConflict):
                await decide_reconciliation(
                    db, project, actor, approval, "stop", "a" * 64
                )
            assert request.checkpoint == original
            assert (
                await db.get(WorkflowExecution, execution)
            ).status == "running"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exhausted_continuation_history_still_allows_a_final_stop():
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        _,
    ) = await interrupted(completed=True)
    try:
        async with sessions() as db:
            request = await db.get(WorkflowApprovalRequest, approval)
            request.reconciliation_ledger = {
                "version": 1,
                "generation": 256,
                "decisions": [
                    {
                        "generation": index,
                        "review_digest": f"{index:064x}",
                        "decision": "continue",
                        "decided_by": actor,
                        "decided_at": datetime.now(UTC).isoformat(),
                        "status": "pending_resume",
                    }
                    for index in range(255)
                ],
            }
            await db.commit()
            review = await get_reconciliation(db, project, actor, approval)
            assert not review.can_continue
            with pytest.raises(ReconciliationConflict):
                await decide_reconciliation(
                    db,
                    project,
                    actor,
                    approval,
                    "continue",
                    review.review_digest,
                )
            result = await decide_reconciliation(
                db, project, actor, approval, "stop", review.review_digest
            )
            assert result.status == "cancelled"
            assert len(request.reconciliation_ledger["decisions"]) == 256
            assert (
                await get_reconciliation_decision(
                    db, project, actor, approval, review.review_digest
                )
                == result
            )
    finally:
        await engine.dispose()
