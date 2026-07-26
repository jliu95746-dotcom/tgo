from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agno.agent import RunCompletedEvent, RunContentEvent

from app.models.internal import Agent as InternalAgent
from app.models.internal import AgentExecutionContext
from app.runtime.supervisor.agents.builder import AgnoAgentBuilder
from app.runtime.supervisor.agents.runner import AgnoAgentRunner
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings


def _build_context() -> AgentExecutionContext:
    now = datetime.now(timezone.utc)
    agent_id = uuid.uuid4()
    agent = InternalAgent(
        id=agent_id,
        name="Support Agent",
        instruction="Base instruction",
        model="openai:gpt-4o",
        config={"temperature": 0.1, "expected_output": "from-agent"},
        tools=[],
        collections=[],
        workflows=[],
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    return AgentExecutionContext(
        agent=agent,
        project_id=str(uuid.uuid4()),
        message="hello",
        system_message="Append this",
        expected_output="Respond in JSON",
        session_id="sess-1",
        user_id="user-1",
        request_id="req-1",
        timeout=30,
        mcp_url="http://mcp",
        rag_url="http://rag",
        enable_memory=True,
        excluded_tool_ids=(uuid.uuid4(),),
    )


@pytest.mark.asyncio
async def test_builder_passes_single_agent_overrides_to_agent_builder(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_build_agent(self, request, internal_agent=None):
        captured["request"] = request
        captured["internal_agent"] = internal_agent
        return SimpleNamespace(id=str(internal_agent.id), name=internal_agent.name)

    monkeypatch.setattr(AgentBuilder, "build_agent", fake_build_agent)

    builder = AgnoAgentBuilder(ToolsRuntimeSettings())
    context = _build_context()

    await builder.build_agent(context)

    request = captured["request"]
    assert request.project_id == context.project_id
    assert request.agent_id == str(context.agent.id)
    assert request.request_id == context.request_id
    assert request.config.system_prompt == context.agent.instruction
    assert request.config.system_message == context.system_message
    assert request.config.expected_output == context.expected_output
    assert request.excluded_tool_ids == context.excluded_tool_ids


@pytest.mark.asyncio
async def test_runner_returns_single_agent_response_shape() -> None:
    context = _build_context()
    built_agent = SimpleNamespace(
        agent=SimpleNamespace(
            arun=AsyncMock(return_value=SimpleNamespace(content="ok", tools=[]))
        )
    )
    runner = AgnoAgentRunner()

    response = await runner.run(built_agent, context)

    assert response.success is True
    assert response.result is not None
    assert response.result.agent_id == context.agent.id
    assert response.result.agent_name == context.agent.name
    assert response.result.content == "ok"
    assert response.content == "ok"
    assert response.metadata is not None
    assert response.metadata.agent_id == context.agent.id
    assert response.metadata.agent_name == context.agent.name


@pytest.mark.asyncio
async def test_runner_returns_only_final_assistant_message_after_tool_call() -> None:
    context = _build_context()
    built_agent = SimpleNamespace(
        agent=SimpleNamespace(
            arun=AsyncMock(
                return_value=SimpleNamespace(
                    content=(
                        "好的，我先查一下知识库。"
                        "目前没有同时满足红色和小羊皮的款式。"
                    ),
                    messages=[
                        SimpleNamespace(role="user", content="找红色小羊皮女包"),
                        SimpleNamespace(
                            role="assistant",
                            content="好的，我先查一下知识库。",
                            tool_calls=[{"function": {"name": "rag_search"}}],
                        ),
                        SimpleNamespace(role="tool", content="检索结果"),
                        SimpleNamespace(
                            role="assistant",
                            content="目前没有同时满足红色和小羊皮的款式。",
                            tool_calls=None,
                        ),
                    ],
                    tools=[],
                )
            )
        )
    )

    response = await AgnoAgentRunner().run(built_agent, context)

    assert response.content == "目前没有同时满足红色和小羊皮的款式。"
    assert "我先查一下" not in response.content


@pytest.mark.asyncio
async def test_stream_uses_content_chunks_when_completed_event_is_empty() -> None:
    async def event_stream():
        yield RunContentEvent(content="链路")
        yield RunContentEvent(content="测试成功")
        yield RunCompletedEvent(content="")

    context = _build_context()
    agent = SimpleNamespace(arun=Mock(return_value=event_stream()))
    workflow_events = Mock()
    runner = AgnoAgentRunner()

    result = await runner.stream(
        SimpleNamespace(agent=agent),
        context,
        workflow_events,
        execution_id="execution-1",
    )

    assert result.content == "链路测试成功"
    assert workflow_events.emit_agent_content_chunk.call_count == 2
    workflow_events.emit_agent_response_complete.assert_called_once_with(
        agent_id=str(context.agent.id),
        agent_name=context.agent.name,
        execution_id="execution-1",
        final_content="链路测试成功",
        success=True,
        total_chunks=2,
        tool_calls_count=0,
    )


@pytest.mark.asyncio
async def test_stream_does_not_duplicate_completed_content_after_chunks() -> None:
    async def event_stream():
        yield RunContentEvent(content="链路")
        yield RunContentEvent(content="测试成功")
        yield RunCompletedEvent(content="链路测试成功")

    context = _build_context()
    agent = SimpleNamespace(arun=Mock(return_value=event_stream()))
    runner = AgnoAgentRunner()

    result = await runner.stream(
        SimpleNamespace(agent=agent),
        context,
        Mock(),
        execution_id="execution-2",
    )

    assert result.content == "链路测试成功"
