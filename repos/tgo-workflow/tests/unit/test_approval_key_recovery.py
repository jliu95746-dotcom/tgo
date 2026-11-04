"""A missing retained key keeps the original approved cursor unconsumed."""

from datetime import UTC, datetime, timedelta
import json

from pydantic import SecretStr
import pytest

from app.config import settings
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.services.approval_state import (
    ApprovalConflict,
    claim_approved_resume,
    decide_approval,
    pause_for_approval,
)
from tests.unit.test_approval_state import prepared_run, preview_for


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["missing", "malformed"])
async def test_missing_key_retains_same_approval_then_recovers(
    monkeypatch, unavailable
):
    old, new = (
        "synthetic-old-approval-" + "a" * 32,
        "synthetic-new-approval-" + "b" * 32,
    )
    monkeypatch.setattr(
        settings, "WORKFLOW_APPROVAL_KEYS", SecretStr(json.dumps({"old": old}))
    )
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", "old")
    engine, sessions, project, execution, actor, cursor = await prepared_run()
    workflow = cursor.workflow_lineage[0]
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db,
                project,
                execution,
                "approval",
                actor,
                "确认",
                cursor,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            await decide_approval(
                db,
                project,
                request.id,
                actor,
                "approve",
                expected_preview_digest=preview_for(request),
            )
            run = await db.get(WorkflowExecution, execution)
            frozen, decision_time = dict(request.checkpoint), request.decided_at
            inputs, started = dict(run.input), run.started_at
        monkeypatch.setattr(
            settings,
            "WORKFLOW_APPROVAL_KEYS",
            SecretStr("{" if unavailable == "malformed" else json.dumps({"new": new})),
        )
        monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", "new")
        async with sessions() as db:
            assert (
                await claim_approved_resume(
                    db,
                    project,
                    execution,
                    workflow,
                    expected_approval_id=request.id,
                    expected_approver_id=actor,
                )
                is None
            )
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            saved = await db.get(WorkflowApprovalRequest, request.id)
            assert (
                run.status == "pending_resume" and run.active_approval_id == request.id
            )
            assert run.started_at == started and run.active_segment_started_at is None
            assert run.input == inputs and saved.checkpoint == frozen
            assert saved.status == "approved" and saved.decided_at == decision_time
            assert "密钥" in run.error and "保留" in run.error
            assert old not in run.error and new not in run.error
        monkeypatch.setattr(
            settings,
            "WORKFLOW_APPROVAL_KEYS",
            SecretStr(json.dumps({"old": old, "new": new})),
        )
        async with sessions() as db:
            resumed = await claim_approved_resume(
                db,
                project,
                execution,
                workflow,
                expected_approval_id=request.id,
                expected_approver_id=actor,
            )
            assert resumed == cursor
            run = await db.get(WorkflowExecution, execution)
            assert run.status == "running" and run.error is None
            assert (
                run.started_at == started and run.active_segment_started_at is not None
            )
            assert (
                await claim_approved_resume(
                    db,
                    project,
                    execution,
                    workflow,
                    expected_approval_id=request.id,
                    expected_approver_id=actor,
                )
                is None
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_corrupt_checkpoint_is_not_reported_as_a_missing_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "WORKFLOW_APPROVAL_KEYS",
        SecretStr(json.dumps({"old": "synthetic-retained-key-" + "c" * 32})),
    )
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", "old")
    engine, sessions, project, execution, actor, cursor = await prepared_run()
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db,
                project,
                execution,
                "approval",
                actor,
                "确认",
                cursor,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            await decide_approval(
                db,
                project,
                request.id,
                actor,
                "approve",
                expected_preview_digest=preview_for(request),
            )
            request.checkpoint = {
                **request.checkpoint,
                "ciphertext": "corrupted-synthetic-cursor",
            }
            await db.commit()
        async with sessions() as db:
            with pytest.raises(ApprovalConflict):
                await claim_approved_resume(
                    db,
                    project,
                    execution,
                    cursor.workflow_lineage[0],
                    expected_approval_id=request.id,
                    expected_approver_id=actor,
                )
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            assert run.status == "pending_resume" and run.error is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_key_notice_cannot_overwrite_a_concurrent_source_stop(monkeypatch):
    from sqlalchemy import update

    old = "synthetic-old-approval-" + "d" * 32
    monkeypatch.setattr(
        settings, "WORKFLOW_APPROVAL_KEYS", SecretStr(json.dumps({"old": old}))
    )
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", "old")
    engine, sessions, project, execution, actor, cursor = await prepared_run()
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db,
                project,
                execution,
                "approval",
                actor,
                "确认",
                cursor,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            await decide_approval(
                db,
                project,
                request.id,
                actor,
                "approve",
                expected_preview_digest=preview_for(request),
            )
        monkeypatch.setattr(
            settings, "WORKFLOW_APPROVAL_KEYS", SecretStr(json.dumps({"new": "n" * 48}))
        )
        monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", "new")
        async with sessions() as db:
            execute = db.execute

            async def revoke_before_notice(statement, *args, **kwargs):
                async with sessions() as other:
                    await other.execute(
                        update(WorkflowExecution)
                        .where(WorkflowExecution.id == execution)
                        .values(status="cancelled", input=None, error="来源已撤销")
                    )
                    await other.commit()
                return await execute(statement, *args, **kwargs)

            monkeypatch.setattr(db, "execute", revoke_before_notice)
            assert (
                await claim_approved_resume(
                    db,
                    project,
                    execution,
                    cursor.workflow_lineage[0],
                    expected_approval_id=request.id,
                    expected_approver_id=actor,
                )
                is None
            )
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            assert (
                run.status == "cancelled" and run.input is None and run.error == "来源已撤销"
            )
    finally:
        await engine.dispose()
