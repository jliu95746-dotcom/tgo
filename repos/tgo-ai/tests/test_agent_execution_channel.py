"""A payload or public header cannot claim a managed execution channel."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from starlette.responses import StreamingResponse

from app.api.v1.agents import router
from app.config import settings
from app.dependencies import get_supervisor_runtime_service
from app.schemas.agent_run import SupervisorRunResponse
from app.services.quota_authorization import require_reply_quota


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("fault", [None, "key", "project", "channel", "payload", "forged_header"])
async def test_only_matching_private_scope_is_forwarded_to_both_execution_paths(monkeypatch, stream, fault):
    project = uuid4()
    monkeypatch.setattr(settings, "secret_key", "synthetic-execution-channel-key")
    runtime = AsyncMock()
    runtime.run.return_value = SupervisorRunResponse(success=True, message="ok", content="ok")
    runtime.stream.return_value = StreamingResponse(iter(["event: disconnected\ndata: {}\n\n"]), media_type="text/event-stream")
    application = FastAPI()
    application.include_router(router, prefix="/api/v1/agents")
    application.dependency_overrides[get_supervisor_runtime_service] = lambda: runtime
    application.dependency_overrides[require_reply_quota] = lambda: None
    headers = {"X-Internal-API-Key": "synthetic-execution-channel-key", "X-Project-Id": str(project), "X-Execution-Channel": "web"}
    body = {"message": "test", "stream": stream, "knowledge_channel": "web"}
    if fault == "key":
        headers["X-Internal-API-Key"] = "wrong"
    elif fault == "project":
        headers["X-Project-Id"] = str(uuid4())
    elif fault == "channel":
        headers["X-Execution-Channel"] = "made_up_channel"
    elif fault == "payload":
        body["knowledge_channel"] = "wecom_kf"
    elif fault == "forged_header":
        headers.pop("X-Internal-API-Key")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.post(f"/api/v1/agents/run?project_id={project}", json=body, headers=headers)
    if fault:
        assert response.status_code == 403
        runtime.run.assert_not_awaited()
        runtime.stream.assert_not_awaited()
    else:
        assert response.status_code == 200
        call = (runtime.stream if stream else runtime.run).call_args
        assert call.args[0].knowledge_channel.value == "web"
        assert call.kwargs["extra_headers"]["X-Execution-Channel"] == "web"
        assert "X-Internal-API-Key" not in call.kwargs["extra_headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("authorized", [False, True])
async def test_business_context_header_is_forwarded_only_from_private_service(monkeypatch, stream, authorized):
    project = uuid4()
    monkeypatch.setattr(settings, "secret_key", "fixture-private-context-key")
    runtime = AsyncMock()
    runtime.run.return_value = SupervisorRunResponse(success=True, message="ok", content="ok")
    runtime.stream.return_value = StreamingResponse(iter(["event: disconnected\ndata: {}\n\n"]))
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/agents")
    app.dependency_overrides[get_supervisor_runtime_service] = lambda: runtime
    app.dependency_overrides[require_reply_quota] = lambda: None
    headers = {"X-Project-Id": str(project), "X-Execution-Channel": "web",
        "X-Business-Request-Context": "private-context-fixture", "X-Business-Request-ID": str(uuid4())}
    if authorized:
        headers["X-Internal-API-Key"] = settings.secret_key
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        response = await client.post(f"/api/v1/agents/run?project_id={project}",
            json={"message": "客户咨询", "stream": stream, "knowledge_channel": "web"}, headers=headers)
    if not authorized:
        assert response.status_code == 403
        runtime.run.assert_not_awaited()
        runtime.stream.assert_not_awaited()
    else:
        assert response.status_code == 200
        call = (runtime.stream if stream else runtime.run).await_args
        assert call.kwargs["extra_headers"]["X-Business-Request-Context"] == headers["X-Business-Request-Context"]
        assert call.kwargs["extra_headers"]["X-Business-Request-ID"] == headers["X-Business-Request-ID"]
