"""Stable source identity and byte-exact internal delivery signatures."""

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.schemas.workflow_event_subscriptions import WorkflowSourceEventRequest
from app.schemas.workflow_events import WorkflowEventTestRequest
from app.services.workflow_client import WorkflowServiceClient
from app.services.workflow_event_signing import sign_source_event


def source_request():
    delivery_id = uuid4()
    return delivery_id, WorkflowSourceEventRequest(
        expected_release_id="release", expected_release_digest="a" * 64,
        data={"excerpt": "客户：物流什么时候更新？", "partial": True},
        source_binding={"subscription_id": uuid4(), "revision": 1,
                        "delivery_id": delivery_id, "source_command_id": uuid4(),
                        "session_id": uuid4()},
    )


def test_retries_preserve_identity_body_and_cover_source_binding():
    delivery_id, request = source_request()
    signed = sign_source_event("flow", "project", delivery_id, request, "test-secret")
    again = sign_source_event("flow", "project", delivery_id, request, "test-secret")
    assert signed.body == again.body
    assert signed.headers["X-Workflow-Event-ID"] == "source-" + str(delivery_id)
    body = json.loads(signed.body)
    assert body["source_binding"] == request.source_binding.model_dump(mode="json")
    assert body["project_id"] == "project" and "request_id" not in body
    prefix = (signed.headers["X-Workflow-Timestamp"] + "\n" +
              signed.headers["X-Workflow-Event-ID"] + "\nflow\n").encode()
    digest = hmac.new(b"test-secret", prefix + signed.body, hashlib.sha256).hexdigest()
    assert signed.headers["X-Workflow-Signature"] == "sha256=" + digest
    with pytest.raises(ValueError):
        sign_source_event("flow", "project", uuid4(), request, "test-secret")


@pytest.mark.asyncio
async def test_client_forwards_exact_signed_body_and_no_actor_override(monkeypatch):
    delivery_id, request = source_request()
    monkeypatch.setattr(settings, "SECRET_KEY", "test-secret")
    client = WorkflowServiceClient()
    remote = AsyncMock(return_value=httpx.Response(202, json={
        "execution_id": "execution", "status": "pending", "deduplicated": False,
    }))
    monkeypatch.setattr(client, "_make_request", remote)
    result = await client.send_source_event("flow", "project", delivery_id, request)
    assert result.execution_id == "execution"
    args, kwargs = remote.call_args
    assert args == ("POST", "/v1/workflows/flow/events")
    assert "json_data" not in kwargs
    assert json.loads(kwargs["raw_body"])["data"] == request.data


def test_browser_test_cannot_forge_source_and_source_body_is_bounded():
    delivery_id, request = source_request()
    body = request.model_dump(mode="json")
    body["request_id"] = str(uuid4())
    with pytest.raises(ValueError):
        WorkflowEventTestRequest.model_validate(body)
    request.data = {"large": "中" * 23000}
    with pytest.raises(HTTPException) as large:
        sign_source_event("flow", "project", delivery_id, request, "test-secret")
    assert large.value.status_code == 413
    request.data = {"invalid": "\ud800"}
    with pytest.raises(HTTPException) as invalid:
        sign_source_event("flow", "project", delivery_id, request, "test-secret")
    assert invalid.value.status_code == 422


def test_signed_source_body_matches_workflow_service_contract():
    path = Path(__file__).parents[2] / "tgo-workflow/app/schemas/event_trigger.py"
    spec = importlib.util.spec_from_file_location("workflow_event_contract", path)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    delivery_id, request = source_request()
    signed = sign_source_event("flow", str(uuid4()), delivery_id, request, "test-secret")
    parsed = contract.WorkflowEventRequest.model_validate_json(signed.body)
    assert parsed.source_binding.model_dump(mode="json") == request.source_binding.model_dump(mode="json")
    assert parsed.data == request.data
