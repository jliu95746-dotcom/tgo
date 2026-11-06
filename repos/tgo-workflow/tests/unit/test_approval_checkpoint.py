"""Confidential continuation state remains bound to its exact approval."""

from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

from pydantic import SecretStr
import pytest

from app.config import settings
from app.engine.checkpoint import ExecutionCheckpoint
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import NodeExecution, WorkflowExecution
from app.services.approval_checkpoint import (
    CheckpointCryptoError,
    open_checkpoint,
    seal_checkpoint,
)
from app.services.approval_state import pause_for_approval
from app.services.execution_snapshot import definition_digest
from app.services.approval_state import claim_approved_resume, decide_approval
from app.services.approval_state import fail_revoked_approved_resume
from app.services.approval_resume_recovery import recover_interrupted_approval
from app.services.approval_preview import approval_preview_digest
from app.services.event_execution_expiry import expire_internal_event
from tests.unit.test_approval_state import prepared_run


@pytest.mark.asyncio
async def test_new_approval_keeps_context_inside_ciphertext(monkeypatch):
    monkeypatch.setattr(
        settings,
        "SECRET_KEY",
        SecretStr("synthetic-approval-secret-" + "a" * 32),
    )
    (
        engine,
        sessions,
        project,
        execution,
        approver,
        checkpoint,
    ) = await prepared_run()
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db,
                project,
                execution,
                "approval",
                approver,
                "确认",
                checkpoint,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            persisted = json.dumps(request.checkpoint)
            assert "ORDER-1" not in persisted
            assert "context_data" not in request.checkpoint
            assert request.checkpoint["capture_status"] == "encrypted"
    finally:
        await engine.dispose()


def encrypted_case():
    project, execution, workflow, approver = (str(uuid4()) for _ in range(4))
    run = WorkflowExecution(
        id=execution,
        project_id=project,
        workflow_id=workflow,
        definition_digest=definition_digest({"nodes": []}),
        workflow_version=2,
        release_id="published-release-2",
        execution_mode="production",
        source_channel="external_webhook",
        source_message_id="delivery-1",
        workflow_lineage=[workflow],
    )
    request = WorkflowApprovalRequest(
        id=str(uuid4()),
        project_id=project,
        execution_id=execution,
        node_id="approval",
        approver_staff_id=approver,
        summary="确认本次结果",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        status="pending",
    )
    checkpoint = ExecutionCheckpoint(
        project_id=project,
        execution_id=execution,
        definition_digest=run.definition_digest,
        input_digest=definition_digest(
            {"data": {"question": "private-phrase"}}
        ),
        workflow_lineage=[workflow],
        context_data={"webhook.question": "private-phrase"},
        current_nodes=[],
        next_nodes=["answer"],
        completed_nodes=["source", "approval"],
        node_index=3,
    )
    request.checkpoint = seal_checkpoint(run, request, checkpoint)
    return run, request, checkpoint


def test_encryption_is_randomized_and_round_trips_the_whole_cursor():
    run, request, checkpoint = encrypted_case()
    first = request.checkpoint
    second = seal_checkpoint(run, request, checkpoint)
    assert first != second
    assert first["key_id"] == "unit"
    assert "private-phrase" not in json.dumps(first)
    assert open_checkpoint(run, request) == checkpoint


@pytest.mark.parametrize(
    "owner,field",
    [
        ("run", "project_id"),
        ("run", "workflow_id"),
        ("run", "id"),
        ("run", "definition_digest"),
        ("run", "workflow_version"),
        ("run", "release_id"),
        ("run", "execution_mode"),
        ("run", "source_channel"),
        ("run", "source_message_id"),
        ("run", "visitor_session_id"),
        ("run", "employee_agent_id"),
        ("run", "business_request_id"),
        ("run", "workflow_lineage"),
        ("request", "id"),
        ("request", "project_id"),
        ("request", "execution_id"),
        ("request", "node_id"),
        ("request", "approver_staff_id"),
        ("request", "summary"),
        ("request", "expires_at"),
    ],
)
def test_changed_owner_or_approved_plan_cannot_open_checkpoint(owner, field):
    run, request, _ = encrypted_case()
    row = run if owner == "run" else request
    value = (
        3
        if field == "workflow_version"
        else ["different-workflow"]
        if field == "workflow_lineage"
        else datetime.now(UTC)
        if field == "expires_at"
        else "changed"
    )
    setattr(row, field, value)
    with pytest.raises(CheckpointCryptoError) as caught:
        open_checkpoint(run, request)
    assert "private-phrase" not in str(caught.value)


def test_corrupt_ciphertext_and_extra_unencrypted_fields_are_rejected():
    run, request, _ = encrypted_case()
    saved = dict(request.checkpoint)
    token = saved["ciphertext"]
    request.checkpoint = {
        **saved,
        "ciphertext": token[:25]
        + ("A" if token[25] != "A" else "B")
        + token[26:],
    }
    with pytest.raises(CheckpointCryptoError):
        open_checkpoint(run, request)
    request.checkpoint = {**saved, "context_data": {"forged": "value"}}
    with pytest.raises(CheckpointCryptoError):
        open_checkpoint(run, request)


def test_rotation_keeps_old_keys_and_uses_the_new_active_key(monkeypatch):
    run, request, checkpoint = encrypted_case()
    old_secret = "synthetic-approval-unit-key-" + "x" * 32
    new_secret = "synthetic-new-approval-key-" + "y" * 32
    keys = {"unit": old_secret, "next": new_secret}
    monkeypatch.setattr(
        settings, "WORKFLOW_APPROVAL_KEYS", SecretStr(json.dumps(keys))
    )
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", "next")
    assert open_checkpoint(run, request) == checkpoint
    assert seal_checkpoint(run, request, checkpoint)["key_id"] == "next"
    retained = dict(request.checkpoint)
    monkeypatch.setattr(
        settings,
        "WORKFLOW_APPROVAL_KEYS",
        SecretStr(json.dumps({"next": new_secret})),
    )
    with pytest.raises(CheckpointCryptoError, match="历史"):
        open_checkpoint(run, request)
    assert request.checkpoint == retained
    monkeypatch.setattr(
        settings, "WORKFLOW_APPROVAL_KEYS", SecretStr(json.dumps(keys))
    )
    assert open_checkpoint(run, request) == checkpoint


def test_secret_key_ciphertext_can_be_imported_under_legacy_key(monkeypatch):
    secret = "synthetic-legacy-approval-key-" + "z" * 32
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_KEYS", None)
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", None)
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr(secret))
    run, request, checkpoint = encrypted_case()
    assert request.checkpoint["key_id"] == "legacy"
    monkeypatch.setattr(
        settings,
        "WORKFLOW_APPROVAL_KEYS",
        SecretStr(json.dumps({"legacy": secret, "next": "n" * 48})),
    )
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", "next")
    assert open_checkpoint(run, request) == checkpoint


def test_missing_keys_do_not_fall_back_to_another_purpose(monkeypatch):
    run, request, checkpoint = encrypted_case()
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_KEYS", None)
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", None)
    monkeypatch.setattr(settings, "SECRET_KEY", None)
    with pytest.raises(CheckpointCryptoError, match="密钥"):
        seal_checkpoint(run, request, checkpoint)


def test_plaintext_is_compatible_only_with_nonexternal_sources():
    run, request, checkpoint = encrypted_case()
    request.checkpoint = checkpoint.model_dump(mode="json")
    for source in ["external_webhook", "internal_event"]:
        run.source_channel = source
        with pytest.raises(CheckpointCryptoError, match="必须加密"):
            open_checkpoint(run, request)
    run.source_channel = "scheduled_timer"
    assert open_checkpoint(run, request) == checkpoint


def test_sqlite_timezone_round_trip_does_not_change_the_scope():
    run, request, checkpoint = encrypted_case()
    request.expires_at = request.expires_at.replace(tzinfo=None)
    assert open_checkpoint(run, request) == checkpoint


def test_nonfinite_or_excessive_context_is_rejected(monkeypatch):
    from app.services import approval_checkpoint as storage

    run, request, checkpoint = encrypted_case()
    checkpoint.context_data = {"value": float("nan")}
    with pytest.raises(CheckpointCryptoError, match="JSON"):
        seal_checkpoint(run, request, checkpoint)
    checkpoint.context_data = {"value": "p" * 3000}
    monkeypatch.setattr(storage, "MAX_CHECKPOINT_BYTES", 2000)
    with pytest.raises(CheckpointCryptoError, match="大小"):
        seal_checkpoint(run, request, checkpoint)


@pytest.mark.asyncio
async def test_approved_checkpoint_survives_claim_recovery_and_scrubs_on_terminal():
    (
        engine,
        sessions,
        project,
        execution,
        approver,
        checkpoint,
    ) = await prepared_run()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            run.source_channel = "scheduled_timer"
            run.execution_mode = "production"
            run.started_at = datetime.now(UTC) - timedelta(hours=1)
            await db.commit()
            request = await pause_for_approval(
                db,
                project,
                execution,
                "approval",
                approver,
                "确认",
                checkpoint,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            await decide_approval(
                db,
                project,
                request.id,
                approver,
                "approve",
                expected_preview_digest=approval_preview_digest(
                    request.id, execution, request.summary
                ),
            )
            await claim_approved_resume(
                db,
                project,
                execution,
                run.workflow_id,
                expected_approval_id=request.id,
                expected_approver_id=approver,
            )
            now = datetime.now(UTC) + timedelta(hours=1)
            assert not await expire_internal_event(
                db, project, execution, now - timedelta(minutes=10), now
            )
            assert await recover_interrupted_approval(
                db, project, execution, cutoff=now - timedelta(minutes=10),
            ) == "requeued"
            await db.refresh(request)
            assert request.checkpoint["capture_status"] != "redacted"
            assert await fail_revoked_approved_resume(db, project, execution, request.id)
            await db.refresh(request)
            assert request.checkpoint["capture_status"] == "redacted"
    finally:
        await engine.dispose()
