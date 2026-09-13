"""Plugin execution must retain the authenticated project and visitor identity."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from agno.tools.function import FunctionCall
from sqlalchemy.ext.asyncio import AsyncSession

from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.services.api_service import APIServiceClient
from app.services.tool_executor import ToolExecutor
from app.models.tool import Tool, ToolType
from app.runtime.tools.config import ToolsRuntimeSettings


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["agent", "direct"])
@pytest.mark.parametrize("suffix", ["", "-vtr", "-staff"])
async def test_plugin_context_reaches_client(monkeypatch, path, suffix):
    project, visitor, agent = str(uuid4()), str(uuid4()), str(uuid4())
    call = AsyncMock(return_value={"success": True, "content": "ok"})
    monkeypatch.setattr("app.services.api_service.api_service_client.execute_plugin_tool", call)
    config = {"plugin_id": "fixture", "tool_name": "query"}
    if path == "agent":
        builder = AgentBuilder(ToolsRuntimeSettings())
        monkeypatch.setattr(builder, "_build_auth_headers", AsyncMock(return_value={}))
        tool = SimpleNamespace(enabled=True, tool_type="MCP", tool_source_type="LOCAL", transport_type="plugin",
                               base_config=config, tool_name="query")
        internal = SimpleNamespace(id=agent, tools=[tool])
        functions = await builder._build_mcp_tools_from_agent(internal, "session", visitor + suffix, project)
        execution = await FunctionCall(function=functions[0], arguments={}).aexecute()
        assert execution.status == "success"
    else:
        from uuid import UUID
        executor = ToolExecutor(cast(AsyncSession, object()), UUID(project))
        executor.set_context(visitor_id=visitor + suffix, session_id="session", agent_id=agent)
        tool = Tool(id=uuid4(), project_id=UUID(project), name="query", tool_type=ToolType.MCP,
                    transport_type="plugin", config=config)
        assert await executor._execute_plugin(tool, {}) == "ok"
    assert call.await_args.kwargs["project_id"] == project
    context = call.await_args.kwargs["context"]
    assert context["visitor_id"] == (None if suffix == "-staff" else visitor)
    assert context["session_id"] == "session"
    assert context["agent_id"] == agent
    assert "user_id" not in context


@pytest.mark.asyncio
async def test_plugin_client_sends_project_as_query_parameter(monkeypatch):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = httpx.Response(200, json={"success": True, "content": "ok"})
    monkeypatch.setattr("app.services.api_service.httpx.AsyncClient", Mock(return_value=client))
    await APIServiceClient().execute_plugin_tool("fixture", "query", {}, {}, project_id="owned")
    assert client.post.await_args.kwargs["params"] == {"project_id": "owned"}


@pytest.mark.asyncio
async def test_plugin_client_rejects_missing_project_before_network(monkeypatch):
    client = Mock()
    monkeypatch.setattr("app.services.api_service.httpx.AsyncClient", client)
    with pytest.raises(ValueError, match="project"):
        await APIServiceClient().execute_plugin_tool("fixture", "query", {}, {})
    client.assert_not_called()
