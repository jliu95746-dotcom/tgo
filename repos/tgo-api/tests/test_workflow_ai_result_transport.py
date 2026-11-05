"""Use the existing HTTP transport; never log or relay private cache bodies."""
from datetime import timedelta
import json
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.schemas.workflow_ai_usage import (
    WorkflowAICacheCommand,
    WorkflowAICacheWrite,
    WorkflowAICacheReceipt,
)
from app.services.workflow_client import WorkflowServiceClient


def command():
    return WorkflowAICacheCommand(
        command={
            "project_id": uuid4(),
            "execution_id": uuid4(),
            "node_id": "agent",
            "payload": {
                "agent_id": uuid4(),
                "message": "synthetic-private-question",
            },
        },
        reservation_id=uuid4(),
        lease_id=uuid4(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["prepare", "read", "store"])
async def test_exact_internal_request_has_bounded_timeout_and_typed_receipt(
    monkeypatch, operation
):
    request = command()
    claim = uuid4()
    result = {
        "success": True,
        "execution_id": uuid4(),
        "content": "synthetic-private-answer",
        "execution_reference": {
            "project_id": request.command.project_id,
            "agent_id": request.command.payload.agent_id,
            "channel": "internal",
            "configuration_fingerprint": "a" * 64,
            "policy_revision": "b" * 64,
        },
    }
    if operation == "store":
        request = WorkflowAICacheWrite(
            **request.model_dump(), claim_id=claim, result=result
        )
    receipt = WorkflowAICacheReceipt(
        state="claimed" if operation == "prepare" else "completed",
        claim_id=claim,
        result=None if operation == "prepare" else result,
    )
    original_client = httpx.AsyncClient
    requests = []

    def respond(incoming):
        requests.append(incoming)
        response = httpx.Response(200, json=receipt.model_dump(mode="json"))
        response.elapsed = timedelta(milliseconds=1)
        return response

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original_client(
            **options,
            transport=httpx.MockTransport(respond),
        ),
    )
    monkeypatch.setattr(
        settings, "SECRET_KEY", "synthetic-result-transport-secret"
    )
    service = WorkflowServiceClient()
    service.base_url = "http://workflow.internal"
    assert await service.workflow_ai_result(operation, request) == receipt
    assert len(requests) == 1
    sent = requests[0]
    assert sent.url.path == "/v1/workflows/ai-runtime/results/" + operation
    assert sent.headers["X-Internal-API-Key"] == settings.SECRET_KEY
    assert sent.extensions["timeout"]["read"] == 15.0
    assert json.loads(sent.content) == request.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 403, 409, 500])
async def test_private_upstream_errors_remain_curated(
    monkeypatch, caplog, status
):
    original_client = httpx.AsyncClient

    def respond(incoming):
        response = httpx.Response(
            status, json={"private": "upstream-private-body"}
        )
        response.elapsed = timedelta(milliseconds=1)
        return response

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original_client(
            **options,
            transport=httpx.MockTransport(respond),
        ),
    )
    service = WorkflowServiceClient()
    service.base_url = "http://workflow.internal"
    with pytest.raises(HTTPException) as error:
        await service.workflow_ai_result("read", command())
    assert error.value.status_code == (502 if status == 200 else status)
    assert "upstream-private-body" not in str(error.value) + caplog.text
    assert "synthetic-private-question" not in caplog.text
