"""An ambiguous response can cause one read, never a second generation."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.integrations import ai_client
from app.integrations.ai_errors import WorkflowAIError
from app.integrations.http_client import HttpClient
from app.schemas.agent_runtime import AgentRuntimeRequest


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setattr(
        ai_client,
        "settings",
        SimpleNamespace(
            SECRET_KEY=SecretStr("synthetic-workflow-recovery-transport-key"),
            API_INTERNAL_SERVICE_URL="http://quota.internal",
        ),
    )
    project, execution, agent = [str(uuid4()) for _ in range(3)]
    return (
        project,
        execution,
        AgentRuntimeRequest(agent_id=agent, message="Synthetic question"),
    )


async def call(scope):
    project, execution, request = scope
    return await ai_client.AIClient.run_agent(
        project,
        request,
        parent_execution_id=execution,
        workflow_node_id="reply",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["timeout", "server_error", "truncated_result", "pending"]
)
async def test_reads_exact_original_answer_after_an_ambiguous_reply(
    scope, monkeypatch, fault
):
    requests = []
    original = {
        "success": True,
        "content": "Original saved answer",
        "execution_id": str(uuid4()),
    }

    def reply(request):
        requests.append(request)
        if request.url.path.endswith("/run"):
            if fault == "timeout":
                raise httpx.ReadTimeout(
                    "private provider marker", request=request
                )
            if fault == "truncated_result":
                return httpx.Response(200, content=b'{"success":true,')
            return httpx.Response(
                409 if fault == "pending" else 503,
                json={"detail": "private marker"},
            )
        assert request.url.path.endswith("/recover")
        return httpx.Response(200, json=original)

    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    monkeypatch.setattr(
        HttpClient, "get_client", AsyncMock(return_value=client)
    )
    try:
        result = await call(scope)
        assert str(result.execution_id) == original["execution_id"]
        assert result.content == original["content"]
        assert [r.url.path for r in requests] == [
            "/internal/workflow-ai/run",
            "/internal/workflow-ai/recover",
        ]
        assert json.loads(requests[0].content) == json.loads(
            requests[1].content
        )
        assert (
            requests[1].headers["X-Internal-API-Key"]
            == "synthetic-workflow-recovery-transport-key"
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 409, 410])
async def test_missing_original_result_stops_without_another_model_call(
    scope, monkeypatch, status, caplog
):
    paths = []

    def reply(request):
        paths.append(request.url.path)
        return httpx.Response(
            503 if len(paths) == 1 else status,
            json={"detail": "private-response-do-not-log"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    monkeypatch.setattr(
        HttpClient, "get_client", AsyncMock(return_value=client)
    )
    try:
        with pytest.raises(WorkflowAIError) as error:
            await call(scope)
        assert error.value.status_code == 409
        assert paths == [
            "/internal/workflow-ai/run",
            "/internal/workflow-ai/recover",
        ]
        assert (
            "private-response-do-not-log" not in str(error.value) + caplog.text
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_explicit_authorization_failure_does_not_attempt_recovery(
    scope, monkeypatch
):
    paths = []

    def reply(request):
        paths.append(request.url.path)
        return httpx.Response(403, json={"detail": "private"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    monkeypatch.setattr(
        HttpClient, "get_client", AsyncMock(return_value=client)
    )
    try:
        with pytest.raises(WorkflowAIError) as error:
            await call(scope)
        assert error.value.status_code == 403
        assert paths == ["/internal/workflow-ai/run"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_user_cancellation_never_starts_a_recovery(scope, monkeypatch):
    requests = []

    async def reply(request):
        requests.append(request)
        raise asyncio.CancelledError()

    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    monkeypatch.setattr(
        HttpClient, "get_client", AsyncMock(return_value=client)
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await call(scope)
        assert len(requests) == 1
    finally:
        await client.aclose()
