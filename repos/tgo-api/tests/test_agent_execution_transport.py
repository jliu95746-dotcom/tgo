"""Both real client paths carry the API-derived channel, never a UI grant."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.core.config import settings
from app.services.ai_client import AIServiceClient


@pytest.mark.asyncio
async def test_sync_execution_carries_private_project_and_channel(monkeypatch):
    project = str(uuid4())
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-execution-hop-key")
    client = AIServiceClient()
    client._make_request = AsyncMock(return_value=httpx.Response(200, json={"success": True, "content": "ok"}))
    await client.run_supervisor_agent("test", project, knowledge_channel="web")
    headers = client._make_request.call_args.kwargs["extra_headers"]
    assert headers == {"X-Internal-API-Key": "synthetic-execution-hop-key", "X-Project-Id": project, "X-Execution-Channel": "web"}
    assert client._make_request.call_args.kwargs["json_data"]["knowledge_channel"] == "web"


@pytest.mark.asyncio
async def test_stream_execution_carries_matching_private_scope_over_http(monkeypatch):
    project = str(uuid4())
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-execution-hop-key")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"event: disconnected\ndata: {}\n\n", headers={"Content-Type": "text/event-stream"})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    client = AIServiceClient()
    client.base_url = "http://owned-test"
    events = [event async for event in client.run_supervisor_agent_stream("test", project, knowledge_channel="wecom_kf")]
    assert events and len(requests) == 1
    assert requests[0].headers["X-Execution-Channel"] == "wecom_kf"
    assert requests[0].headers["X-Project-Id"] == project
    assert requests[0].headers["X-Internal-API-Key"] == "synthetic-execution-hop-key"


def test_missing_external_channel_defaults_to_internal_and_unknown_channel_is_rejected():
    project = str(uuid4())
    assert AIServiceClient._execution_headers(project, None)["X-Execution-Channel"] == "internal"
    with pytest.raises(ValueError):
        AIServiceClient._execution_headers(project, "made_up_channel")
