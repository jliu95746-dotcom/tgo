"""Device debug uses the configured agent, never a second device-side LLM."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import device_control
from app.core.security import get_current_active_user
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.services.ai_client import ai_client


@pytest.fixture
def setup(monkeypatch):
    project, device, agent = (str(uuid4()) for _ in range(3))
    monkeypatch.setattr(
        device_control.device_control_client,
        "get_device",
        AsyncMock(
            return_value={
                "id": device,
                "project_id": project,
                "status": "online",
            }
        ),
    )
    monkeypatch.setattr(
        ai_client,
        "get_agent",
        AsyncMock(
            return_value={
                "id": agent,
                "bound_device_id": device,
                "model": "configured-model",
            }
        ),
    )
    calls, closed = [], []

    async def stream(**kwargs):
        calls.append(kwargs)
        try:
            yield "connected", {"request_id": "owned"}
            yield "event", {
                "event_type": "agent_content_chunk",
                "data": {"content_chunk": '你好\n"引号"'},
            }
            yield "event", {"event_type": "workflow_completed", "data": {}}
        finally:
            closed.append(True)

    monkeypatch.setattr(ai_client, "run_supervisor_agent_stream", stream)
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(device_control.router, prefix="/v1/device-control")
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(project_id=project)
    return SimpleNamespace(
        app=app,
        project=project,
        device=device,
        agent=agent,
        calls=calls,
        closed=closed,
        body={"device_id": device, "agent_id": agent, "message": "测试"},
    )


async def send(setup, **changes):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=setup.app), base_url="http://test"
    ) as client:
        return await client.post(
            "/v1/device-control/chat", json={**setup.body, **changes}
        )


@pytest.mark.asyncio
async def test_debug_reuses_agent_stream_and_preserves_sse(setup):
    response = await send(setup, system_prompt="仅检查")
    assert response.status_code == 200, response.text
    assert "event: connected\n" in response.text
    assert "event: event\n" in response.text
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[1]["data"]["content_chunk"] == '你好\n"引号"'
    assert (
        sum(
            event.get("event_type") == "workflow_completed" for event in events
        )
        == 1
    )
    assert setup.calls == [
        {
            "message": "测试",
            "project_id": setup.project,
            "agent_id": setup.agent,
            "enable_memory": False,
            "system_message": "仅检查",
            "expected_device_id": setup.device,
        }
    ]
    assert setup.closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,status",
    [
        ("offline", 409),
        ("wrong-binding", 409),
        ("foreign-device", 404),
        ("missing-device", 404),
    ],
)
async def test_invalid_device_does_not_start_model(setup, case, status):
    if case == "offline":
        device_control.device_control_client.get_device.return_value[
            "status"
        ] = "offline"
    elif case == "wrong-binding":
        ai_client.get_agent.return_value["bound_device_id"] = str(uuid4())
    elif case == "foreign-device":
        device_control.device_control_client.get_device.return_value[
            "project_id"
        ] = str(uuid4())
    else:
        device_control.device_control_client.get_device.return_value = None
    response = await send(setup)
    assert response.status_code == status, response.text
    assert setup.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"agent_id": None},
        {"device_id": "bad"},
        {"message": "   "},
        {"max_iterations": 4},
        {"extra": True},
        {"model": "other"},
    ],
)
async def test_invalid_or_unsupported_override_is_explicit(setup, changes):
    response = await send(setup, **changes)
    assert response.status_code in (409, 422), response.text
    assert setup.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending",
    ["truncated", "failed", "exception", "bad-event", "transport-error"],
)
async def test_failed_stream_never_reports_completion(
    setup, monkeypatch, ending
):
    async def stream(**kwargs):
        try:
            if ending == "exception":
                raise httpx.ConnectError(
                    'private-token="secret"\nprivate-address'
                )
            if ending == "failed":
                yield "event", {
                    "event_type": "workflow_failed",
                    "data": {"error_message": "失败"},
                }
            elif ending == "bad-event":
                yield "event\ndata: forged", {"hello": "bad"}
            elif ending == "transport-error":
                yield "error", {"error": "upstream failed"}
            else:
                yield "connected", {"request_id": "owned"}
        finally:
            setup.closed.append(True)

    monkeypatch.setattr(ai_client, "run_supervisor_agent_stream", stream)
    response = await send(setup)
    assert response.status_code == 200, response.text
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert "workflow_completed" not in response.text
    assert (
        events[-1].get("event_type") in ("workflow_failed", "error")
        or "error" in events[-1]
    )
    assert "secret" not in response.text and "forged" not in response.text
    assert setup.closed == [True]


@pytest.mark.asyncio
async def test_cancellation_closes_upstream_stream(setup, monkeypatch):
    from app.services.device_debug_chat import stream_device_debug

    entered = asyncio.Event()

    async def stream(**kwargs):
        try:
            entered.set()
            await asyncio.Event().wait()
            yield "event", {}
        finally:
            setup.closed.append(True)

    monkeypatch.setattr(ai_client, "run_supervisor_agent_stream", stream)
    request = device_control.DeviceDebugChatRequest(**setup.body)
    iterator = stream_device_debug(request, setup.project)
    task = asyncio.create_task(anext(iterator))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert setup.closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("fenced", [False, True])
async def test_ai_client_sends_device_fence_only_when_requested(
    monkeypatch, fenced
):
    from app.services.ai_client import AIServiceClient

    project, device = str(uuid4()), str(uuid4())
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(
            200,
            text='event: event\ndata: {"event_type":"workflow_completed"}\n\n',
            headers={"Content-Type": "text/event-stream"},
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(handle), **kwargs
        ),
    )
    client = AIServiceClient()
    options = {"expected_device_id": device} if fenced else {}
    events = [
        event
        async for event in client.run_supervisor_agent_stream(
            message="测试", project_id=project, **options
        )
    ]
    assert events == [("event", {"event_type": "workflow_completed"})]
    assert observed[0].url.path == "/api/v1/agents/run"
    assert observed[0].url.params["project_id"] == project
    body = json.loads(observed[0].content)
    assert ("expected_device_id" in body) is fenced
    if fenced:
        assert body["expected_device_id"] == device
