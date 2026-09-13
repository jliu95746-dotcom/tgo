"""Tool runtime streaming must finish once and retain tool failures."""

from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from agno.agent import RunCompletedEvent, RunContentEvent, ToolCallCompletedEvent
from agno.models.response import ToolExecution

from app.models.streaming import EventSeverity
from app.runtime.supervisor.agents.runner import AgnoAgentRunner
from app.runtime.supervisor.infrastructure.services import AgentServiceClient
from app.runtime.supervisor.streaming.workflow_events import WorkflowEventEmitter
from app.runtime.tools.executor.service import ToolsRuntimeService
from app.runtime.tools.models import (
    AgentRunRequest, AgentRunResponse, CompleteStreamEvent,
    ContentStreamEvent, ErrorStreamEvent,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("intermediate", [False, True])
async def test_runtime_requests_terminal_events_and_preserves_tool_result(intermediate: bool) -> None:
    captured: dict[str, object] = {}

    async def arun(*args: object, **kwargs: object) -> AsyncIterator[object]:
        captured.update(kwargs)
        yield RunContentEvent(content="回复")
        if kwargs.get("stream_events"):
            yield ToolCallCompletedEvent(tool=ToolExecution(
                tool_call_id="call_1", tool_name="query", tool_args={},
                result="查询失败", tool_call_error=True,
            ))
            yield RunCompletedEvent(content="回复")

    runtime = ToolsRuntimeService()
    runtime._builder.build_agent = AsyncMock(return_value=SimpleNamespace(arun=arun))
    events = [event async for event in runtime.stream_agent(AgentRunRequest(
        message="测试", stream=True, stream_intermediate_steps=intermediate,
    ))]
    assert captured.get("stream_events") is True
    assert "stream_intermediate_steps" not in captured
    terminal = [event for event in events if event.event == "complete"]
    assert len(terminal) == 1
    assert terminal[0].final_response.tools[0].tool_call_error is True
    assert sum(event.event == "tool_call" for event in events) == int(intermediate)


@pytest.mark.asyncio
async def test_missing_completion_event_is_not_silently_successful() -> None:
    async def arun(*args: object, **kwargs: object) -> AsyncIterator[object]:
        yield RunContentEvent(content="未完成")

    runtime = ToolsRuntimeService()
    runtime._builder.build_agent = AsyncMock(return_value=SimpleNamespace(arun=arun))
    events = [event async for event in runtime.stream_agent(AgentRunRequest(message="测试"))]
    assert events[-1].event == "error"
    assert events[-1].error_type == "IncompleteStream"


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_supervisor_completion_snapshot_does_not_duplicate_text_or_clear_error(failed: bool) -> None:
    async def stream(*args: object) -> AsyncIterator[object]:
        yield ContentStreamEvent(content="完整")
        yield ContentStreamEvent(content="回复")
        if failed:
            yield ErrorStreamEvent(error="连接断开")
        yield CompleteStreamEvent(final_response=AgentRunResponse(content="完整回复", success=True))

    adapter = AgentServiceClient(SimpleNamespace(stream_agent=stream))
    adapter._build_agent_run_request = Mock(return_value=AgentRunRequest(message="测试"))
    result = await adapter.execute_agent_streaming(
        agent=SimpleNamespace(id=uuid4(), name="owned-fixture", config={}),
        request=SimpleNamespace(session_id=None, user_id=None), auth_headers={},
        workflow_events=Mock(), execution_id="owned-execution",
    )
    assert result.content == "完整回复"
    assert result.success is (not failed)
    assert result.error == ("连接断开" if failed else None)


@pytest.mark.parametrize("failed", [False, True])
def test_tool_completion_event_does_not_mark_failure_as_success(failed: bool) -> None:
    emitter = Mock()
    WorkflowEventEmitter(emitter).emit_agent_tool_call_completed(
        agent_id="agent", agent_name="客服", execution_id="run", tool_name="query",
        tool_output="查询失败" if failed else "运输中", tool_call_error=failed,
    )
    _, data, severity, _ = emitter.emit.call_args.args
    assert data.status == ("failed" if failed else "completed")
    assert data.error == ("查询失败" if failed else None)
    assert severity is (EventSeverity.ERROR if failed else EventSeverity.SUCCESS)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_main_agent_runner_forwards_tool_error_flag(failed: bool) -> None:
    async def arun(*args: object, **kwargs: object) -> AsyncIterator[object]:
        yield ToolCallCompletedEvent(tool=ToolExecution(
            tool_name="query", tool_args={}, result="结果", tool_call_error=failed,
        ))
        yield RunContentEvent(content="回复")
        yield RunCompletedEvent(content="回复")

    events = Mock()
    result = await AgnoAgentRunner().stream(
        SimpleNamespace(agent=SimpleNamespace(arun=arun)),
        SimpleNamespace(message="测试", session_id=None, user_id=None,
                        agent=SimpleNamespace(id=uuid4(), name="owned-fixture")),
        events, "owned-execution",
    )
    assert result.content == "回复"
    assert events.emit_agent_tool_call_completed.call_args.kwargs["tool_call_error"] is failed


@pytest.mark.asyncio
async def test_main_agent_runner_does_not_publish_truncated_stream_as_success() -> None:
    async def arun(*args: object, **kwargs: object) -> AsyncIterator[object]:
        yield RunContentEvent(content="只有半句")

    events = Mock()
    result = await AgnoAgentRunner().stream(
        SimpleNamespace(agent=SimpleNamespace(arun=arun)),
        SimpleNamespace(message="测试", session_id=None, user_id=None,
                        agent=SimpleNamespace(id=uuid4(), name="owned-fixture")),
        events, "owned-execution",
    )
    assert result.success is False
    assert result.error == "Agent stream ended without a completion event"
    assert events.emit_agent_response_complete.call_args.kwargs["success"] is False
