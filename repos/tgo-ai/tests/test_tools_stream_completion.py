"""Tool runtime streaming must finish once and retain tool failures."""

from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from agno.agent import RunCompletedEvent, RunContentEvent, ToolCallCompletedEvent
from agno.models.response import ToolExecution

from app.models.streaming import (
    AgentBusinessQueryLink, EventSeverity, EventType, StreamingEvent,
)
from app.runtime.supervisor.agents.runner import AgnoAgentRunner
from app.runtime.supervisor.infrastructure.services import AgentServiceClient
from app.runtime.supervisor.streaming.workflow_events import WorkflowEventEmitter
from app.runtime.tools.executor.service import ToolsRuntimeService
from app.runtime.tools.models import (
    AgentRunRequest, AgentRunResponse, CompleteStreamEvent,
    ContentStreamEvent, ErrorStreamEvent,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("buffered, expected_checks", [(True, 5), (False, 7)])
async def test_buffered_draft_coalesces_only_nearby_content_checks(
    buffered: bool, expected_checks: int,
) -> None:
    ticks = [0.0]

    async def arun(*args: object, **kwargs: object) -> AsyncIterator[object]:
        yield RunContentEvent(content="一")
        yield RunContentEvent(content="二")
        ticks[0] = 0.6
        yield RunContentEvent(content="三")
        yield ToolCallCompletedEvent(
            tool=ToolExecution(tool_name="query", result="结果"),
        )
        yield RunContentEvent(content="四")
        yield RunCompletedEvent(content="一二三四")

    events = Mock()
    context = SimpleNamespace(
        message="测试", session_id=None, user_id=None, project_id=str(uuid4()),
        response_purpose="standard", knowledge_evidence=None,
        agent=SimpleNamespace(id=uuid4(), name="客服", model="openai:gpt-4o"),
    )
    runner_module = "app.runtime.supervisor.agents.runner"
    with (
        patch(f"{runner_module}.monotonic", side_effect=lambda: ticks[0]),
        patch(
            f"{runner_module}.check_current_execution", new_callable=AsyncMock,
        ) as check,
    ):
        result = await AgnoAgentRunner().stream(
            SimpleNamespace(agent=SimpleNamespace(arun=arun)), context, events,
            "owned-execution", buffered_draft=buffered,
        )

    assert check.await_count == expected_checks
    assert result.content == "一二三四"
    assert events.emit_agent_content_chunk.call_count == 4
    events.emit_agent_tool_call_completed.assert_called_once()
    events.emit_agent_response_complete.assert_called_once()


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


def test_business_query_link_survives_stream_event_serialization() -> None:
    query_id = uuid4()
    link = AgentBusinessQueryLink(
        tool_name=f"employee_business_member_{uuid4().hex}", query_id=query_id,
        operation="membership_query", outcome="passed",
    )
    emitter = Mock()
    WorkflowEventEmitter(emitter).emit_agent_tool_call_completed(
        agent_id="agent", agent_name="客服", execution_id=str(uuid4()),
        tool_name=link.tool_name, business_query_links=[link],
    )
    _, data, severity, metadata = emitter.emit.call_args.args
    event = StreamingEvent(
        event_type=EventType.AGENT_TOOL_CALL_COMPLETED,
        correlation_id="corr", request_id="request", severity=severity,
        data=data, metadata=metadata,
    )
    payload = event.model_dump(mode="json")
    assert payload["data"]["business_query_links"] == [{
        "tool_name": link.tool_name, "query_id": str(query_id),
        "operation": "membership_query", "outcome": "passed",
    }]
    assert "order_no" not in str(payload)


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
                        project_id=str(uuid4()), response_purpose="standard",
                        knowledge_evidence=None,
                        agent=SimpleNamespace(id=uuid4(), name="owned-fixture",
                                              model="openai:gpt-4o")),
        events, "owned-execution",
    )
    assert result.content == "回复"
    assert events.emit_agent_tool_call_completed.call_args.kwargs["tool_call_error"] is failed
    assert events.emit_agent_tool_call_completed.call_args.kwargs["tool_output"] == "结果"


@pytest.mark.asyncio
async def test_main_agent_runner_does_not_publish_truncated_stream_as_success() -> None:
    async def arun(*args: object, **kwargs: object) -> AsyncIterator[object]:
        yield RunContentEvent(content="只有半句")

    events = Mock()
    result = await AgnoAgentRunner().stream(
        SimpleNamespace(agent=SimpleNamespace(arun=arun)),
        SimpleNamespace(message="测试", session_id=None, user_id=None,
                        project_id=str(uuid4()), response_purpose="standard",
                        knowledge_evidence=None,
                        agent=SimpleNamespace(id=uuid4(), name="owned-fixture",
                                              model="openai:gpt-4o")),
        events, "owned-execution",
    )
    assert result.success is False
    assert result.error == "Agent stream ended without a completion event"
    assert events.emit_agent_response_complete.call_args.kwargs["success"] is False
