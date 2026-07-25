"""Per-run tool exclusions must not mutate the persisted agent binding."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models.internal import Agent, AgentTool
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import AgentConfig


def _agent_with_two_tools() -> tuple[Agent, AgentTool, AgentTool]:
    now = datetime.now(UTC)
    excluded = AgentTool(
        tool_id=uuid4(),
        tool_name="express_service",
        tool_type="MCP",
        endpoint="http://store/express/http",
    )
    retained = AgentTool(
        tool_id=uuid4(),
        tool_name="order_service",
        tool_type="MCP",
        endpoint="http://store/order/http",
    )
    agent = Agent(
        id=uuid4(),
        project_id=str(uuid4()),
        name="Support Agent",
        instruction="Help customers.",
        model="openai:gpt-4o",
        tools=[excluded, retained],
        collections=[],
        workflows=[],
        created_at=now,
        updated_at=now,
    )
    return agent, excluded, retained


@pytest.mark.asyncio
async def test_build_tools_filters_only_the_tools_excluded_for_this_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = AgentBuilder(ToolsRuntimeSettings())
    agent, excluded, retained = _agent_with_two_tools()

    monkeypatch.setattr(builder, "_build_rag_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_workflow_tools", AsyncMock(return_value=[]))
    build_mcp = AsyncMock(return_value=[])
    monkeypatch.setattr(builder, "_build_mcp_tools_from_agent", build_mcp)
    monkeypatch.setattr(builder, "_build_custom_tools", Mock(return_value=[]))

    await builder._build_tools(
        AgentConfig(),
        session_id="session-1",
        user_id="visitor-1",
        internal_agent=agent,
        project_id=agent.project_id,
        agent_id=str(agent.id),
        request_id="request-1",
        excluded_tool_ids=(excluded.tool_id,),
    )

    runtime_agent = build_mcp.await_args.args[0]
    assert [tool.tool_id for tool in runtime_agent.tools] == [retained.tool_id]
    assert [tool.tool_id for tool in agent.tools] == [
        excluded.tool_id,
        retained.tool_id,
    ]
