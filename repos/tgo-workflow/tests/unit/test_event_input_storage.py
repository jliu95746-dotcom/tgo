"""Retained input is private; source authority stays readable."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select

from app.config import settings
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import NodeExecution, WorkflowExecution
from app.services import event_trigger
from app.services.approval_inputs import approval_input_digest
from app.services.event_inputs import (
    EventInputKeyUnavailable,
    frozen_event_inputs,
    seal_event_inputs,
)
from app.services.execution_lifecycle import ExecutionStopped
from app.services.execution_snapshot import definition_digest
from celery_app import tasks
from tests.unit.test_event_source_binding import (
    binding,
    request_for,
    source_store as _shared_source_store,
)

source_store = _shared_source_store


@pytest.mark.asyncio
async def test_new_source_event_never_persists_customer_body_in_plaintext(
    source_store,
):
    sessions, project, workflow, context, _ = source_store
    source = binding()
    request = request_for(project, context, source)
    request.data = {"excerpt": "synthetic-private-customer-body-20261025"}
    async with sessions() as db:
        accepted = await event_trigger.accept_event(
            db,
            workflow,
            request,
            "source-" + str(source.delivery_id),
            "synthetic-http-authentication",
        )
        run = await db.get(WorkflowExecution, accepted.execution_id)
        serialized = json.dumps(run.input)
        assert request.data["excerpt"] not in serialized
        assert "data" not in run.input
        assert run.input["_event_source"] == source.model_dump(mode="json")
        assert frozen_event_inputs(run) == {
            "data": request.data,
            "_event_source": source.model_dump(mode="json"),
        }
        assert await approval_input_digest(db, run) == definition_digest(
            frozen_event_inputs(run),
        )


@pytest.mark.asyncio
async def test_legacy_plaintext_requires_its_original_invocation_proof(
    source_store, monkeypatch
):
    sessions, project, workflow, context, _ = source_store
    legacy_secret = "synthetic-legacy-shared-authentication-key"
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr(legacy_secret))
    source = binding()
    request = request_for(project, context, source)
    async with sessions() as db:
        accepted = await event_trigger.accept_event(
            db,
            workflow,
            request,
            "source-" + str(source.delivery_id),
            legacy_secret,
        )
        run = await db.get(WorkflowExecution, accepted.execution_id)
        # A genuine pre-encryption record used the same receipt proof.
        run.input = {
            "data": request.data,
            "_event_source": source.model_dump(mode="json"),
        }
        assert frozen_event_inputs(run) == run.input
        run.input["data"] = {"excerpt": "forged-legacy-body"}
        with pytest.raises(ValueError):
            frozen_event_inputs(run)


def keyring(monkeypatch, keys, active):
    monkeypatch.setattr(
        settings, "WORKFLOW_EVENT_INPUT_KEYS", SecretStr(json.dumps(keys))
    )
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID", active)


def prepared_encrypted_run():
    source = binding()
    run = WorkflowExecution(
        id=str(uuid4()),
        project_id=str(uuid4()),
        workflow_id=str(uuid4()),
        source_channel="internal_event",
        execution_mode="production",
        release_id=str(uuid4()),
        definition_digest="d" * 64,
        workflow_version=1,
        source_message_id="source-" + str(source.delivery_id),
        visitor_session_id=str(source.session_id),
        invocation_input_hmac="e" * 64,
    )
    inputs = {
        "data": {"excerpt": "synthetic-confidential-body"},
        "_event_source": source.model_dump(mode="json"),
    }
    run.input = seal_event_inputs(run, inputs)
    return run, inputs


def test_independent_key_rotation_never_discards_unreadable_body(monkeypatch):
    old, new = (
        "synthetic-old-event-key-" + "o" * 32,
        "synthetic-new-event-key-" + "n" * 32,
    )
    keyring(monkeypatch, {"old": old}, "old")
    run, expected = prepared_encrypted_run()
    frozen = json.dumps(run.input, sort_keys=True)
    keyring(monkeypatch, {"new": new}, "new")
    with pytest.raises(EventInputKeyUnavailable):
        frozen_event_inputs(run)
    assert json.dumps(run.input, sort_keys=True) == frozen
    keyring(
        monkeypatch,
        {"old": "wrong-retained-key-" + "x" * 32, "new": new},
        "new",
    )
    with pytest.raises(EventInputKeyUnavailable):
        frozen_event_inputs(run)
    assert json.dumps(run.input, sort_keys=True) == frozen
    keyring(monkeypatch, {"old": old, "new": new}, "new")
    assert frozen_event_inputs(run) == expected
    second = seal_event_inputs(run, expected)
    assert second["_event_payload"]["key_id"] == "new"
    assert second != run.input


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", "foreign-project"),
        ("workflow_id", "foreign-flow"),
        ("id", "foreign-run"),
        ("source_message_id", "other-event"),
        ("release_id", "other-release"),
        ("definition_digest", "f" * 64),
        ("workflow_version", 2),
        ("source_channel", "external_webhook"),
        ("execution_mode", "draft_test"),
        ("visitor_session_id", str(uuid4())),
        ("event_receipt_key_id", "other-key"),
        ("invocation_input_hmac", "a" * 64),
        ("workflow_lineage", ["other-flow"]),
    ],
)
def test_encrypted_body_cannot_be_rebound_to_another_run(field, value):
    run, _ = prepared_encrypted_run()
    setattr(run, field, value)
    with pytest.raises(ValueError):
        frozen_event_inputs(run)


@pytest.mark.parametrize(
    "field",
    [
        "revision",
        "subscription_id",
        "delivery_id",
        "source_command_id",
        "session_id",
    ],
)
def test_encrypted_body_cannot_change_source_binding(field):
    run, _ = prepared_encrypted_run()
    run.input["_event_source"][field] = (
        2 if field == "revision" else str(uuid4())
    )
    with pytest.raises(ValueError):
        frozen_event_inputs(run)


def test_legacy_fallback_can_be_imported_without_plaintext_downgrade(
    monkeypatch,
):
    fallback = "synthetic-legacy-key-" + "l" * 32
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_INPUT_KEYS", None)
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID", None)
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr(fallback))
    run, inputs = prepared_encrypted_run()
    assert run.input["_event_payload"]["key_id"] == "legacy"
    keyring(
        monkeypatch,
        {"legacy": fallback, "new": "synthetic-new-" + "x" * 32},
        "new",
    )
    assert frozen_event_inputs(run) == inputs
    run.input["data"] = inputs["data"]
    with pytest.raises(ValueError):
        frozen_event_inputs(run)


@pytest.mark.asyncio
async def test_worker_retains_unclaimed_input_until_historical_key_returns(
    source_store,
    monkeypatch,
):
    sessions, project, workflow, context, _ = source_store
    async with sessions.kw["bind"].begin() as connection:
        await connection.run_sync(NodeExecution.__table__.create)
    source = binding()
    request = request_for(project, context, source)
    async with sessions() as db:
        accepted = await event_trigger.accept_event(
            db,
            workflow,
            request,
            "source-" + str(source.delivery_id),
            "secret",
        )
        before = json.dumps(
            (await db.get(WorkflowExecution, accepted.execution_id)).input
        )
    retained = settings.WORKFLOW_EVENT_INPUT_KEYS
    keyring(
        monkeypatch, {"next": "synthetic-next-input-key-" + "x" * 32}, "next"
    )
    monkeypatch.setattr(tasks, "workflow_task_session", sessions)
    await tasks._run_workflow(accepted.execution_id, workflow, {}, project)
    async with sessions() as db:
        run = await db.get(WorkflowExecution, accepted.execution_id)
        assert run.status == "pending"
        assert json.dumps(run.input) == before
        assert "密钥" in run.error
        assert (await db.scalars(select(NodeExecution))).all() == []
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_INPUT_KEYS", retained)
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID", "unit")
    await tasks._run_workflow(accepted.execution_id, workflow, {}, project)
    await tasks._run_workflow(accepted.execution_id, workflow, {}, project)
    async with sessions() as db:
        run = await db.get(WorkflowExecution, accepted.execution_id)
        assert run.status == "completed" and run.input is None
        assert run.error is None
        assert (await db.scalars(select(NodeExecution.node_id))).all() == [
            "event",
            "answer",
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["pending", "running", "waiting_approval", "pending_resume"]
)
async def test_revocation_scrubs_without_decrypting_either_key(
    source_store,
    monkeypatch,
    status,
):
    sessions, project, workflow, context, verify = source_store
    source = binding()
    async with sessions() as db:
        accepted = await event_trigger.accept_event(
            db,
            workflow,
            request_for(project, context, source),
            "source-" + str(source.delivery_id),
            "secret",
        )
        run = await db.get(WorkflowExecution, accepted.execution_id)
        run.status = status
        approval = WorkflowApprovalRequest(
            project_id=project,
            execution_id=run.id,
            node_id="approval",
            approver_staff_id=str(uuid4()),
            summary="合成待确认业务",
            status="approved" if status == "pending_resume" else "pending",
            checkpoint={
                "capture_status": "encrypted",
                "key_id": "missing",
                "ciphertext": "synthetic-unreadable-checkpoint",
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(approval)
        await db.flush()
        run.active_approval_id = approval.id
        await db.commit()
        verify.side_effect = PermissionError("source_revoked")
        keyring(
            monkeypatch, {"next": "synthetic-next-key-" + "x" * 32}, "next"
        )
        with pytest.raises(ExecutionStopped):
            await event_trigger.require_active_internal_event(
                db,
                project,
                workflow,
                run.id,
                expected_status=status,
            )
        await db.refresh(run)
        await db.refresh(approval)
        assert run.status == "cancelled" and run.input is None
        assert run.output is None
        assert approval.checkpoint["capture_status"] == "redacted"


@pytest.mark.asyncio
async def test_event_preparation_reports_unavailable_input_key(
    source_store, monkeypatch
):
    sessions, project, workflow, _, _ = source_store
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_INPUT_KEYS", None)
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID", None)
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("too-short"))
    async with sessions() as db:
        with pytest.raises(HTTPException) as error:
            await event_trigger.event_test_context(db, workflow, project)
        assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_release", [False, True])
async def test_legacy_manual_event_proof_keeps_both_release_binding_formats(
    source_store,
    monkeypatch,
    explicit_release,
):
    sessions, project, workflow, context, _ = source_store
    secret = "synthetic-legacy-manual-event-key"
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr(secret))
    request = request_for(project, context)
    if not explicit_release:
        request.expected_release_id = None
        request.expected_release_digest = None
    async with sessions() as db:
        accepted = await event_trigger.accept_event(
            db,
            workflow,
            request,
            "legacy-manual-proof",
            secret,
        )
        run = await db.get(WorkflowExecution, accepted.execution_id)
        run.input = {"data": request.data}
        assert frozen_event_inputs(run) == {"data": request.data}
        run.project_id = "another-project"
        with pytest.raises(ValueError):
            frozen_event_inputs(run)


@pytest.mark.asyncio
async def test_unreadable_pending_source_still_expires_at_retention_limit(
    source_store,
    monkeypatch,
):
    sessions, project, workflow, context, _ = source_store
    source = binding()
    async with sessions() as db:
        accepted = await event_trigger.accept_event(
            db,
            workflow,
            request_for(project, context, source),
            "source-" + str(source.delivery_id),
            "secret",
        )
        run = await db.get(WorkflowExecution, accepted.execution_id)
        run.started_at = datetime.now(UTC) - timedelta(minutes=31)
        await db.commit()
    keyring(monkeypatch, {"next": "synthetic-next-key-" + "x" * 32}, "next")
    monkeypatch.setattr(tasks, "workflow_task_session", sessions)
    await tasks._run_workflow(accepted.execution_id, workflow, {}, project)
    async with sessions() as db:
        run = await db.get(WorkflowExecution, accepted.execution_id)
        assert run.status == "failed" and run.input is None
        assert "30 分钟" in run.error
