"""Source events are pinned to a currently authorized subscription and release."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.integrations import workflow_event_source_client as source_client
from app.models.execution import WorkflowExecution
from app.models.approval import WorkflowApprovalRequest
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.schemas.event_trigger import (
    WorkflowEventRequest, WorkflowEventSourceBinding, WorkflowEventSourceVerification,
)
from app.services import event_trigger, workflow_releases
from app.services.execution_lifecycle import ExecutionStopped
from app.services.event_inputs import frozen_event_inputs
from app.schemas.workflow_release import WorkflowPublishRequest


def binding():
    return WorkflowEventSourceBinding(
        subscription_id=uuid4(), revision=1, delivery_id=uuid4(),
        source_command_id=uuid4(), session_id=uuid4(),
    )


def definition():
    return {"nodes": [
        {"id": "event", "type": "event", "data": {
            "label": "结束会话", "reference_key": "event",
            "event_type": "staff.session.closed",
        }},
        {"id": "answer", "type": "answer", "data": {
            "label": "结果", "reference_key": "answer", "output_type": "template",
            "output_template": "已收到事件",
        }},
    ], "edges": [{"source": "event", "target": "answer"}]}


@pytest.fixture
async def source_store(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for table in (
            Workflow.__table__, WorkflowRelease.__table__,
            WorkflowExecution.__table__, WorkflowApprovalRequest.__table__,
        ):
            await connection.run_sync(table.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, workflow_id = str(uuid4()), str(uuid4())
    async with sessions() as db:
        db.add(Workflow(id=workflow_id, project_id=project_id, name="会话归纳",
                        status="draft", definition=definition(), version=1, tags=[]))
        await db.commit()
        await workflow_releases.publish(
            db, workflow_id, project_id,
            WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0),
            str(uuid4()),
        )
        await db.commit()
        context = await event_trigger.event_test_context(db, workflow_id, project_id)
    verify = AsyncMock()
    monkeypatch.setattr(event_trigger, "verify_event_source", verify)
    monkeypatch.setattr(event_trigger, "admit_event", AsyncMock())
    monkeypatch.setattr(event_trigger, "enqueue_pending_initial", AsyncMock())
    try:
        yield sessions, project_id, workflow_id, context, verify
    finally:
        await engine.dispose()


def request_for(project_id, context, source=None):
    return WorkflowEventRequest(
        project_id=project_id, event_type=context.event_type,
        expected_release_id=context.release_id,
        expected_release_digest=context.release_digest,
        data={"summary": "private content"}, source_binding=source,
    )


@pytest.mark.parametrize("change", [
    {"event_type": "order.shipped"}, {"expected_release_id": None, "expected_release_digest": None},
    {"source_binding": {"revision": 0}},
])
def test_source_request_rejects_incomplete_or_wrong_binding(change):
    data = {"project_id": str(uuid4()), "event_type": "staff.session.closed",
            "expected_release_id": str(uuid4()), "expected_release_digest": "a" * 64,
            "source_binding": binding().model_dump(mode="json")}
    data.update(change)
    with pytest.raises(ValidationError):
        WorkflowEventRequest.model_validate(data)


@pytest.mark.asyncio
async def test_source_receipt_and_session_binding_survive_duplicate_after_scrub(source_store):
    sessions, project, workflow, context, verify = source_store
    source = binding()
    request = request_for(project, context, source)
    event_id = f"source-{source.delivery_id}"
    async with sessions() as db:
        accepted = await event_trigger.accept_event(db, workflow, request, event_id, "secret")
        run = await db.get(WorkflowExecution, accepted.execution_id)
        assert frozen_event_inputs(run) == {"data": request.data, "_event_source": source.model_dump(mode="json")}
        assert run.visitor_session_id == str(source.session_id)
        verify.assert_awaited_once()
        run.status, run.input = "completed", None
        await db.commit()
        verify.side_effect = PermissionError("disabled")
        duplicate = await event_trigger.accept_event(db, workflow, request, event_id, "secret")
        assert duplicate.deduplicated
        assert verify.await_count == 1
        changed = request.model_copy(update={"source_binding": source.model_copy(update={"revision": 2})})
        with pytest.raises(HTTPException) as conflict:
            await event_trigger.accept_event(db, workflow, changed, event_id, "secret")
        assert conflict.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status", [(PermissionError("private"), 409), (RuntimeError("private"), 503)])
async def test_source_rejected_before_creation_when_authority_unavailable(source_store, error, status):
    sessions, project, workflow, context, verify = source_store
    verify.side_effect = error
    source = binding()
    async with sessions() as db:
        with pytest.raises(HTTPException) as rejected:
            await event_trigger.accept_event(db, workflow, request_for(project, context, source), f"source-{source.delivery_id}", "s")
        assert rejected.value.status_code == status
        assert "private" not in str(rejected.value.detail)
        event_trigger.enqueue_pending_initial.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_event_never_calls_source_authority(source_store):
    sessions, project, workflow, context, verify = source_store
    async with sessions() as db:
        accepted = await event_trigger.accept_event(db, workflow, request_for(project, context), "manual", "s")
        run = await db.get(WorkflowExecution, accepted.execution_id)
        run.status = "running"
        await db.commit()
        await event_trigger.require_active_internal_event(db, project, workflow, run.id)
        verify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change,expected_status", [
    ("disabled", "cancelled"), ("release", "cancelled"),
    ("authority", "cancelled"), ("network", "failed"), ("malformed", "cancelled"),
])
async def test_before_node_revocation_or_unverifiable_source_stops_and_scrubs(source_store, change, expected_status):
    sessions, project, workflow, context, verify = source_store
    source = binding()
    async with sessions() as db:
        accepted = await event_trigger.accept_event(db, workflow, request_for(project, context, source), f"source-{source.delivery_id}", "s")
        run = await db.get(WorkflowExecution, accepted.execution_id)
        run.status = "running"
        flow = await db.get(Workflow, workflow)
        if change == "disabled":
            flow.status = "inactive"
        elif change == "release":
            flow.published_version = 2
        elif change == "authority":
            verify.side_effect = PermissionError("private details")
        elif change == "network":
            verify.side_effect = RuntimeError("private details")
        elif change == "malformed":
            run.input = {"data": {}, "_event_source": {"revision": 0}}
        await db.commit()
        with pytest.raises(ExecutionStopped):
            await event_trigger.require_active_internal_event(db, project, workflow, run.id)
        await db.refresh(run)
        assert run.status == expected_status
        assert run.input is None
        assert "private" not in (run.error or "")


@pytest.mark.asyncio
async def test_source_current_authority_checked_before_each_node(source_store):
    sessions, project, workflow, context, verify = source_store
    source = binding()
    async with sessions() as db:
        accepted = await event_trigger.accept_event(db, workflow, request_for(project, context, source), f"source-{source.delivery_id}", "s")
        run = await db.get(WorkflowExecution, accepted.execution_id)
        run.status = "running"
        await db.commit()
        await event_trigger.require_active_internal_event(db, project, workflow, run.id)
        await event_trigger.require_active_internal_event(db, project, workflow, run.id)
        assert verify.await_count == 3


@pytest.mark.asyncio
async def test_source_delivery_cannot_create_another_run_under_different_event_id(source_store):
    sessions, project, workflow, context, verify = source_store
    async with sessions() as db:
        with pytest.raises(HTTPException) as rejected:
            await event_trigger.accept_event(db, workflow, request_for(project, context, binding()), "different-id", "s")
        assert rejected.value.status_code == 409
        verify.assert_not_awaited()
        event_trigger.enqueue_pending_initial.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "project", "revision", "session", "release", "extra", "unavailable", "revoked", "timeout"])
async def test_source_http_receipt_must_echo_entire_scope(monkeypatch, change):
    query = WorkflowEventSourceVerification(
        project_id=uuid4(), workflow_id=str(uuid4()), event_type="staff.session.closed",
        release_id=str(uuid4()), release_digest="a" * 64, source_binding=binding(),
    )
    monkeypatch.setattr(source_client.settings, "SECRET_KEY", SecretStr("unit-key"))

    def respond(request):
        assert request.url.path.endswith("/internal/workflows/events/source/verify")
        assert request.headers["X-Internal-API-Key"] == "unit-key"
        payload = json.loads(request.content)
        assert payload == query.model_dump(mode="json")
        if change == "timeout":
            raise httpx.ReadTimeout("private details")
        if change in {"unavailable", "revoked"}:
            return httpx.Response(503 if change == "unavailable" else 409, text="private details")
        if change == "project":
            payload["project_id"] = str(uuid4())
        if change == "revision":
            payload["source_binding"]["revision"] += 1
        if change == "session":
            payload["source_binding"]["session_id"] = str(uuid4())
        if change == "release":
            payload["release_digest"] = "b" * 64
        if change == "extra":
            payload["unknown"] = "field"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(source_client.HttpClient, "get_client", AsyncMock(return_value=client))
        if change is None:
            await source_client.verify_event_source(query)
        else:
            with pytest.raises((PermissionError, RuntimeError)) as rejected:
                await source_client.verify_event_source(query)
            assert "private details" not in str(rejected.value)
