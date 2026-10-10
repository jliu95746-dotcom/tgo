"""Cancellation owns the actual execution, not a model-library lookup key."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import httpx
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.run.cancel import get_active_runs

from app.exceptions import ExternalServiceError
from app.runtime.supervisor.application import service as runtime_module
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.runtime.supervisor.agents.runner import AgnoAgentRunner
from app.schemas.agent_run import SupervisorRunRequest
from app.services import device_execution as monitor
from app.streaming.event_emitter import StreamingEventEmitter


async def make_execution(
    monkeypatch,
    mode,
    *,
    swallow=False,
    finish_held=False,
    real_agent=None,
    timeout=None,
):
    project, device = uuid4(), uuid4()
    bound = mode != "plain"
    context = SimpleNamespace(
        project_id=str(project),
        message="隔离测试",
        session_id=None,
        user_id=None,
        request_id="fixture",
        disable_tools=False,
        response_purpose="standard",
        knowledge_evidence=None,
        agent=SimpleNamespace(
            id=uuid4(), name="测试", model="openai:fixture",
            bound_device_id=str(device) if bound else None,
        ),
    )
    started, finish_started, release_finish = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    if not finish_held:
        release_finish.set()

    async def finish(*args):
        finish_started.set()
        await release_finish.wait()

    client = SimpleNamespace(
        start_session=AsyncMock(),
        heartbeat_session=AsyncMock(),
        finish_session=AsyncMock(side_effect=finish),
    )
    monkeypatch.setattr(monitor, "device_control_client", client)
    emitter = StreamingEventEmitter(str(uuid4()), str(uuid4()))
    monkeypatch.setattr(runtime_module, "get_event_emitter", lambda *args: emitter)
    runtime = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock(_settings=None)
    )
    runtime._prepare_context = AsyncMock(return_value=(context, str(context.agent.id)))
    runtime._agent_builder.build_agent = AsyncMock(
        return_value=SimpleNamespace(agent=real_agent or SimpleNamespace())
    )
    state = SimpleNamespace(
        task=None,
        run_id=None,
        scope=None,
        headers=None,
        followup=False,
        natural_finish=asyncio.Event(),
    )

    async def run(agent, ctx, events, execution_id):
        state.task = asyncio.current_task()
        state.run_id = execution_id
        state.scope = monitor.current_device_execution()
        if bound:
            state.headers = monitor.device_headers_factory(str(project), str(device))
        started.set()
        if real_agent is not None:
            return await AgnoAgentRunner().stream(agent, ctx, events, execution_id)
        try:
            await state.natural_finish.wait()
        except asyncio.CancelledError:
            if not swallow:
                raise
        else:
            state.followup = True
        return SimpleNamespace(success=True, total_time=0.01, error=None)

    runtime._agent_runner.stream = AsyncMock(side_effect=run)
    request = SupervisorRunRequest(
        message="隔离测试", expected_device_id=device if mode == "debug" else None
    )
    if timeout is not None:
        request = request.model_copy(update={"timeout": timeout})
    response = await runtime.stream(
        request,
        project,
        http_request=SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
    )
    await asyncio.wait_for(started.wait(), 1)
    return SimpleNamespace(
        runtime=runtime,
        project=project,
        state=state,
        response=response,
        client=client,
        emitter=emitter,
        release_finish=release_finish,
        finish_started=finish_started,
    )


async def dispose(execution):
    execution.release_finish.set()
    if not execution.state.task.done():
        execution.state.task.cancel()
    await asyncio.gather(execution.state.task, return_exceptions=True)


async def domain_events(response):
    async def collect():
        return [chunk async for chunk in response.body_iterator]

    chunks = await asyncio.wait_for(collect(), 2)
    return [
        json.loads(chunk.split("data: ", 1)[1].strip())
        for chunk in chunks
        if chunk.startswith("event: event\n")
    ]


@pytest.mark.parametrize("mode", ["plain", "bound", "debug"])
@pytest.mark.parametrize("swallow", [False, True])
async def test_stop_cancels_owner_and_emits_one_terminal(monkeypatch, mode, swallow):
    execution = await make_execution(monkeypatch, mode, swallow=swallow)
    runtime, state, project = execution.runtime, execution.state, execution.project
    try:
        assert not await runtime.cancel(state.run_id, uuid4())
        assert not await runtime.cancel(str(uuid4()), project)
        assert not state.task.done()
        assert await runtime.cancel(state.run_id, project)
        # A repeated click must not inject another cancellation into cleanup.
        assert await runtime.cancel(state.run_id, project)
        if mode != "plain":
            assert state.scope.closed
            with pytest.raises(ExternalServiceError, match="已经结束"):
                state.headers()
        await asyncio.wait_for(asyncio.shield(state.task), 1)
        assert not state.followup
        assert not runtime._runs
        assert not await runtime.cancel(state.run_id, project)
        events = await domain_events(execution.response)
        terminals = [
            item for item in events if item["event_type"].startswith("workflow_")
        ]
        assert [item["event_type"] for item in terminals] == [
            "workflow_started",
            "workflow_failed",
        ]
        assert terminals[-1]["data"]["error_message"] == "任务已停止"
        if mode != "plain":
            execution.client.finish_session.assert_awaited_once_with(
                state.scope.identity, "cancelled"
            )
    finally:
        await dispose(execution)


async def test_repeat_stop_during_cleanup_does_not_abort_finish(monkeypatch):
    execution = await make_execution(monkeypatch, "bound", finish_held=True)
    runtime, state = execution.runtime, execution.state
    try:
        assert await runtime.cancel(state.run_id, execution.project)
        await asyncio.wait_for(execution.finish_started.wait(), 1)
        assert await runtime.cancel(state.run_id, execution.project)
        assert state.task.cancelling() == 1
        assert not state.task.done()
        execution.release_finish.set()
        await asyncio.wait_for(asyncio.shield(state.task), 1)
        assert execution.client.finish_session.await_count == 1
        assert not runtime._runs
    finally:
        await dispose(execution)


async def test_stop_after_execution_finished_does_not_relabel_success(monkeypatch):
    execution = await make_execution(monkeypatch, "bound", finish_held=True)
    runtime, state = execution.runtime, execution.state
    try:
        state.natural_finish.set()
        await asyncio.wait_for(execution.finish_started.wait(), 1)
        assert not await runtime.cancel(state.run_id, execution.project)
        execution.release_finish.set()
        await asyncio.wait_for(asyncio.shield(state.task), 1)
        execution.client.finish_session.assert_awaited_once_with(
            state.scope.identity, "completed"
        )
        assert not runtime._runs
    finally:
        await dispose(execution)


async def test_real_agno_tool_loop_stops_during_next_model_request(monkeypatch):
    """Actual Agno/OpenAI adapter, deterministic in-process HTTP, no provider calls."""
    model_waiting, model_cancelled = asyncio.Event(), asyncio.Event()
    requests, tool_calls = [], []

    async def isolated_tool() -> str:
        """Return an isolated fixture result without touching a real device."""
        tool_calls.append("fixture")
        return "fixture result"

    async def model_response(request):
        assert request.url.host == "fixture.invalid"
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) > 1:
            assert any(item["role"] == "tool" for item in payload["messages"])
            model_waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                model_cancelled.set()
        common = {
            "id": "fixture",
            "created": 1,
            "model": "fixture",
            "object": "chat.completion.chunk",
        }
        delta = {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "fixture-tool",
                    "type": "function",
                    "function": {"name": "isolated_tool", "arguments": "{}"},
                }
            ],
        }
        chunks = [
            {
                **common,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                **common,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(body + "data: [DONE]\n\n").encode(),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(model_response)
    ) as client:
        agent = Agent(
            model=OpenAIChat(
                id="fixture",
                api_key="fixture-only",
                base_url="http://fixture.invalid/v1",
                http_client=client,
                max_retries=0,
            ),
            tools=[isolated_tool],
            telemetry=False,
        )
        execution = await make_execution(monkeypatch, "plain", real_agent=agent)
        try:
            await asyncio.wait_for(model_waiting.wait(), 3)
            assert execution.state.run_id in get_active_runs()
            assert await execution.runtime.cancel(
                execution.state.run_id, execution.project
            )
            await asyncio.wait_for(asyncio.shield(execution.state.task), 2)
            assert model_cancelled.is_set()
            assert execution.state.run_id not in get_active_runs()
            assert tool_calls == ["fixture"] and len(requests) == 2
            events = await domain_events(execution.response)
            assert sum(item["event_type"] == "workflow_failed" for item in events) == 1
            assert not any(
                item["event_type"] == "workflow_completed" for item in events
            )
        finally:
            await dispose(execution)


async def test_debug_timeout_after_registration_keeps_single_timeout_terminal(
    monkeypatch,
):
    execution = await make_execution(monkeypatch, "debug", timeout=0.02)
    try:
        events = await domain_events(execution.response)
        failures = [item for item in events if item["event_type"] == "workflow_failed"]
        assert len(failures) == 1
        assert "调试超时" in failures[0]["data"]["error_message"]
        assert not execution.runtime._runs
        assert not execution.state.followup
        assert execution.state.scope.closed
    finally:
        await dispose(execution)
