"""Only tenant administrators can send a bounded, signed event test."""

import hashlib
import hmac
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from app.api.v1.endpoints.workflow_events import router, workflow_client
from app.core.security import get_current_active_user
from app.core.exceptions import validation_exception_handler
from app.core.config import settings
from app.schemas.workflow_events import WorkflowEventTestRequest
from app.services.workflow_client import WorkflowServiceClient


def event_request():
    return {"request_id": str(uuid4()), "event_type": "consultation.received",
            "expected_release_id": str(uuid4()), "expected_release_digest": "a" * 64,
            "data": {"咨询": "这款包是什么材质？"}}


@pytest.mark.asyncio
async def test_admin_proxy_scopes_project_actor_and_forbids_overrides(monkeypatch):
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin", username="test-admin")
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    accepted = {"execution_id": str(uuid4()), "status": "pending", "deduplicated": False}
    send = AsyncMock(return_value=accepted)
    context = AsyncMock(return_value={"workflow_id": "flow", "event_type": "consultation.received",
        "release_id": str(uuid4()), "release_digest": "a" * 64, "version": 1, "status": "active"})
    monkeypatch.setattr(workflow_client, "send_event_test", send)
    monkeypatch.setattr(workflow_client, "get_event_test_context", context)
    payload = event_request()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/ai/workflows/flow/event-tests", json=payload)
        assert response.status_code == 202 and response.json() == accepted
        assert response.headers["cache-control"] == "no-store"
        send.assert_awaited_once_with("flow", str(staff.project_id), str(staff.id),
                                     WorkflowEventTestRequest.model_validate(payload))
        preview = await client.get("/v1/ai/workflows/flow/event-test-context", params={"project_id": "forged"})
        assert preview.status_code == 200
        context.assert_awaited_once_with("flow", str(staff.project_id))
        for field in ("project_id", "actor_staff_id", "secret", "url"):
            assert (await client.post("/v1/ai/workflows/flow/event-tests",
                json={**payload, field: "forged"})).status_code == 422
        staff.role = "user"
        assert (await client.post("/v1/ai/workflows/flow/event-tests", json=payload)).status_code == 403
        assert (await client.get("/v1/ai/workflows/flow/event-test-context")).status_code == 403
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_transport_sends_identical_signed_bytes_and_scoped_retry_id(monkeypatch):
    secret = "unit-event-test-secret"
    monkeypatch.setattr(settings, "SECRET_KEY", secret)
    captured = []

    def handle(request):
        captured.append(request)
        response = httpx.Response(202, json={"execution_id": "run", "status": "pending", "deduplicated": False})
        response.elapsed = timedelta(0)
        return response

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs))
    service = WorkflowServiceClient()
    request = WorkflowEventTestRequest.model_validate(event_request())
    for actor in ("staff-a", "staff-a", "staff-b"):
        await service.send_event_test("flow", "project-a", actor, request)
    first, repeated, other_actor = captured
    assert first.content == repeated.content
    assert first.headers["X-Workflow-Event-ID"] == repeated.headers["X-Workflow-Event-ID"]
    assert first.headers["X-Workflow-Event-ID"] != other_actor.headers["X-Workflow-Event-ID"]
    signed = (first.headers["X-Workflow-Timestamp"] + "\n" + first.headers["X-Workflow-Event-ID"]
              + "\nflow\n").encode() + first.content
    assert first.headers["X-Workflow-Signature"] == "sha256=" + hmac.new(
        secret.encode(), signed, hashlib.sha256).hexdigest()
    body = json.loads(first.content)
    assert body["project_id"] == "project-a"
    assert body["expected_release_id"] == request.expected_release_id
    assert body["expected_release_digest"] == request.expected_release_digest
    assert body["data"] == request.data and secret.encode() not in first.content
    assert first.url.path == "/v1/workflows/flow/events"


@pytest.mark.asyncio
async def test_size_and_finite_validation_prevent_internal_request(monkeypatch):
    service = WorkflowServiceClient()
    upstream = AsyncMock()
    monkeypatch.setattr(service, "_make_request", upstream)
    payload = event_request()
    payload["data"] = {"long": "测" * 65536}
    with pytest.raises(HTTPException) as large:
        await service.send_event_test("flow", "project-a", "staff-a",
                                      WorkflowEventTestRequest.model_validate(payload))
    assert large.value.status_code == 413
    upstream.assert_not_awaited()
    payload["data"] = {"nested": [float("nan")]}
    with pytest.raises(ValueError):
        WorkflowEventTestRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_context_transport_authenticates_and_does_not_send_input(monkeypatch):
    service = WorkflowServiceClient()
    upstream = AsyncMock(return_value=httpx.Response(200, json={
        "workflow_id": "flow", "event_type": "consultation.received", "release_id": "release",
        "release_digest": "a" * 64, "version": 2, "status": "active",
    }))
    monkeypatch.setattr(service, "_make_request", upstream)
    result = await service.get_event_test_context("flow", "project-a")
    assert result.version == 2
    upstream.assert_awaited_once_with("GET", "/v1/workflows/flow/event-test-context",
        params={"project_id": "project-a"}, extra_headers={"X-Internal-API-Key": settings.SECRET_KEY})


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", "1e999"])
async def test_invalid_data_is_redacted_by_real_validation_handler(monkeypatch, invalid, caplog):
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.dependency_overrides[get_current_active_user] = lambda: staff
    send = AsyncMock()
    monkeypatch.setattr(workflow_client, "send_event_test", send)
    request = event_request()
    request["data"] = {"private": "do-not-log-customer-text"}
    body = json.dumps(request)[:-1] + ', "data":{"private":"do-not-log-customer-text","n":' + invalid + '}}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/ai/workflows/flow/event-tests", content=body,
                                     headers={"content-type": "application/json"})
    assert response.status_code == 422, response.text
    assert "do-not-log-customer-text" not in response.text
    for record in caplog.records:
        assert "do-not-log-customer-text" not in str(record.__dict__)
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["data", "expected_release_id"])
async def test_invalid_unicode_never_reaches_internal_transport(monkeypatch, field):
    service = WorkflowServiceClient()
    upstream = AsyncMock()
    monkeypatch.setattr(service, "_make_request", upstream)
    payload = event_request()
    payload[field] = {"text": chr(0xd800)} if field == "data" else chr(0xd800)
    if field == "expected_release_id":
        with pytest.raises(ValueError):
            WorkflowEventTestRequest.model_validate(payload)
        upstream.assert_not_awaited()
        return
    with pytest.raises(HTTPException) as invalid:
        await service.send_event_test("flow", "project-a", "staff-a",
                                      WorkflowEventTestRequest.model_validate(payload))
    assert invalid.value.status_code == 422
    upstream.assert_not_awaited()
