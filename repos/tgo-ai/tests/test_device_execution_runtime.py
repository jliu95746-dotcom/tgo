"""Both supervisor entry points own the same device lifecycle contract."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.responses import StreamingResponse

from app.exceptions import ExternalServiceError
from app.runtime.supervisor.application import service as runtime_module
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.schemas.agent_run import SupervisorRunRequest
from app.services import device_execution as monitor


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "outcome", ["success", "failed", "build_error", "start_error"]
)
async def test_supervisor_owns_full_device_execution(
    monkeypatch, streaming, outcome
):
    project, device = uuid4(), uuid4()
    ctx = SimpleNamespace(
        project_id=str(project),
        message="隔离测试",
        request_id="fixture",
        disable_tools=False,
        response_purpose="standard",
        agent=SimpleNamespace(
            id=uuid4(), name="测试", bound_device_id=str(device)
        ),
    )
    client = SimpleNamespace(
        start_session=AsyncMock(),
        heartbeat_session=AsyncMock(),
        finish_session=AsyncMock(),
    )
    if outcome == "start_error":
        client.start_session.side_effect = ExternalServiceError(
            "device-control", "无法创建记录"
        )
    monkeypatch.setattr(monitor, "device_control_client", client)
    runtime = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock(_settings=None)
    )
    runtime._prepare_context = AsyncMock(return_value=(ctx, str(ctx.agent.id)))
    states = []

    async def build(context):
        execution = monitor.current_device_execution()
        assert execution is not None and not execution.closed
        states.append(execution)
        if outcome == "build_error":
            raise ValueError("build failed")
        return SimpleNamespace(agent=Mock())

    async def run(*args):
        execution = monitor.current_device_execution()
        assert execution is states[0] and not execution.closed
        client.finish_session.assert_not_awaited()
        if streaming:
            assert args[3] == str(execution.identity.session_id)
        return SimpleNamespace(
            success=outcome == "success",
            total_time=0.01,
            error=None if outcome == "success" else "fixture failed",
        )

    runtime._agent_builder.build_agent = AsyncMock(side_effect=build)
    runtime._agent_runner.run = AsyncMock(side_effect=run)
    runtime._agent_runner.stream = AsyncMock(side_effect=run)
    request = SupervisorRunRequest(message="隔离测试", expected_device_id=device)
    if streaming:
        terminal = asyncio.Event()
        events = Mock()

        def ended(*args):
            if outcome != "start_error":
                client.finish_session.assert_awaited_once()
            terminal.set()

        events.emit_workflow_failed.side_effect = ended
        events.emit_workflow_completed.side_effect = ended
        monkeypatch.setattr(
            runtime_module, "create_workflow_events", Mock(return_value=events)
        )
        monkeypatch.setattr(
            runtime_module, "get_event_emitter", Mock(return_value=Mock())
        )

        async def body():
            await asyncio.wait_for(terminal.wait(), 2)
            yield "done"

        monkeypatch.setattr(
            runtime_module,
            "create_sse_response",
            lambda *args: StreamingResponse(body()),
        )
        response = await runtime.stream(request, project, http_request=Mock())
        _ = [chunk async for chunk in response.body_iterator]
        assert events.emit_workflow_completed.call_count == int(
            outcome == "success"
        )
        assert events.emit_workflow_failed.call_count == int(
            outcome != "success"
        )
        assert not runtime._runs
    else:
        response = await runtime.run(request, project)
        assert response.success == (outcome == "success")
    if outcome == "start_error":
        runtime._agent_builder.build_agent.assert_not_awaited()
        client.finish_session.assert_not_awaited()
    else:
        assert states[0].closed
        client.finish_session.assert_awaited_once_with(
            states[0].identity,
            "completed" if outcome == "success" else "failed",
        )
    assert monitor.current_device_execution() is None
