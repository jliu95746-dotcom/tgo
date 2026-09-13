"""Device debugging must not outlive its stream or switch the target device."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.responses import StreamingResponse

from app.runtime.supervisor.application import service as runtime_module
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.schemas.agent_run import SupervisorRunRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("matching", [False, True])
async def test_device_fence_checks_the_resolved_agent_before_build(
    monkeypatch, matching
):
    device, project, agent = uuid4(), uuid4(), uuid4()
    bound = str(device if matching else uuid4())
    resolved = SimpleNamespace(id=agent, bound_device_id=bound)

    @asynccontextmanager
    async def service_context():
        yield Mock()

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get_agent.return_value = resolved
    monkeypatch.setattr(
        runtime_module, "AIServiceClient", Mock(return_value=client)
    )
    runtime = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock(_settings=None)
    )
    runtime._agent_service_context = service_context
    monkeypatch.setattr(
        runtime_module,
        "AgentExecutionContext",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    request = SupervisorRunRequest(
        agent_id=str(agent), message="测试", expected_device_id=device
    )
    if matching:
        context, _ = await runtime._prepare_context(
            request, project, {"X-Request-ID": "owned"}
        )
        assert context.agent.bound_device_id == str(device)
    else:
        with pytest.raises(ValueError, match="设备绑定已变化"):
            await runtime._prepare_context(
                request, project, {"X-Request-ID": "owned"}
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnected", [False, True])
async def test_owned_response_always_awaits_execution_cleanup(disconnected):
    from app.streaming.owned_response import own_execution

    started, stopped = asyncio.Event(), asyncio.Event()

    async def execution():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def body():
        await started.wait()
        yield "data: started\n\n"
        if disconnected:
            await asyncio.Event().wait()

    task = asyncio.create_task(execution())
    response = own_execution(StreamingResponse(body()), task)
    sent = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body":
            sent.set()

    async def receive():
        await sent.wait()
        if disconnected:
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()

    await asyncio.wait_for(
        response(
            {"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send
        ),
        2,
    )
    assert task.done() and stopped.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [False, True])
async def test_runtime_terminal_status_matches_execution(monkeypatch, success):
    runtime = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock(_settings=None)
    )
    context = SimpleNamespace(
        agent=SimpleNamespace(id=uuid4(), name="测试", bound_device_id=None),
        message="测试", disable_tools=False, response_purpose="standard",
    )
    runtime._prepare_context = AsyncMock(return_value=(context, "agent"))
    runtime._agent_builder.build_agent = AsyncMock(
        return_value=SimpleNamespace(agent=Mock())
    )
    runtime._agent_runner.stream = AsyncMock(
        return_value=SimpleNamespace(
            success=success, error=None if success else "执行失败", total_time=0.1
        )
    )
    events = Mock()
    monkeypatch.setattr(
        runtime_module, "create_workflow_events", Mock(return_value=events)
    )
    monkeypatch.setattr(
        runtime_module, "get_event_emitter", Mock(return_value=Mock())
    )

    async def body():
        for _ in range(100):
            if (
                events.emit_workflow_completed.called
                or events.emit_workflow_failed.called
            ):
                break
            await asyncio.sleep(0)
        yield "done"

    monkeypatch.setattr(
        runtime_module,
        "create_sse_response",
        lambda *args: StreamingResponse(body()),
    )
    response = await runtime.stream(
        SupervisorRunRequest(message="测试"), uuid4(), http_request=Mock()
    )
    _ = [chunk async for chunk in response.body_iterator]
    assert events.emit_workflow_completed.call_count == int(success)
    assert events.emit_workflow_failed.call_count == int(not success)


@pytest.mark.asyncio
async def test_device_timeout_cancels_preparation(monkeypatch):
    runtime = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock(_settings=None)
    )
    stopped = asyncio.Event()

    async def prepare(*args):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    runtime._prepare_context = prepare
    events = Mock()
    monkeypatch.setattr(
        runtime_module, "create_workflow_events", Mock(return_value=events)
    )
    monkeypatch.setattr(
        runtime_module, "get_event_emitter", Mock(return_value=Mock())
    )

    async def body():
        await asyncio.wait_for(stopped.wait(), 1)
        for _ in range(100):
            if events.emit_workflow_failed.called:
                break
            await asyncio.sleep(0)
        yield "done"

    monkeypatch.setattr(
        runtime_module,
        "create_sse_response",
        lambda *args: StreamingResponse(body()),
    )
    request = SupervisorRunRequest(
        message="测试", expected_device_id=uuid4()
    ).model_copy(update={"timeout": 0.01})
    response = await runtime.stream(request, uuid4(), http_request=Mock())
    _ = [chunk async for chunk in response.body_iterator]
    assert stopped.is_set()
    assert events.emit_workflow_failed.call_count == 1
    assert not events.emit_workflow_completed.called
