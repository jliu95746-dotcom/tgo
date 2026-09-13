"""Enabled MCP bindings must never silently disappear from an agent run."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models.internal import AgentTool
from app.runtime.core.exceptions import MCPToolError
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import AgentConfig, AgentRunRequest
from app.runtime.tools.executor.service import ToolsRuntimeService


def binding(endpoint="https://fixture.invalid/rpc", name="query"):
    return AgentTool(tool_id=uuid4(), tool_name=name, tool_type="MCP",
                     transport_type="http", endpoint=endpoint, enabled=True)


@pytest.mark.asyncio
async def test_bound_setup_error_reaches_caller_without_credentials(monkeypatch):
    builder = AgentBuilder(ToolsRuntimeSettings())
    monkeypatch.setattr(builder, "_build_rag_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_workflow_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_mcp_tools_from_agent", AsyncMock(
        side_effect=ValueError("Authorization: Bearer fixture-secret")))
    builder._logger = Mock()
    with pytest.raises(MCPToolError, match="已绑定的工具暂时不可用") as raised:
        await builder._build_tools(AgentConfig(), None, None,
                                   internal_agent=SimpleNamespace(tools=[binding()]))
    assert "fixture-secret" not in str(raised.value)
    assert "fixture-secret" not in str(builder._logger.mock_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connect", "missing", "cancel"])
@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_failed_server_closes_prior_connections_in_reverse_order(monkeypatch, failure, transport):
    closed = []

    class FakeMCP:
        def __init__(self, *, url, **kwargs):
            self.url = url
            self.functions = {"query": object()}

        async def connect(self):
            if self.url.endswith("third"):
                if failure == "connect":
                    raise OSError("Authorization: Bearer fixture-secret")
                if failure == "cancel":
                    raise asyncio.CancelledError()
                self.functions = {}

        async def close(self):
            closed.append(self.url.rsplit("/", 1)[-1])

    monkeypatch.setattr("app.runtime.tools.builder.agent_builder.SavedMCPTools", FakeMCP)
    builder = AgentBuilder(ToolsRuntimeSettings())
    builder._logger = Mock()
    groups = {f"https://fixture.invalid/{name}": [binding().model_copy(update={"transport_type": transport})]
              for name in ["first", "second", "third"]}
    expected = asyncio.CancelledError if failure == "cancel" else MCPToolError
    with pytest.raises(expected):
        await builder._build_mcp_server_instances(groups, {})
    # A connected server missing the selected tool also owns a connection to close.
    assert closed == (["third", "second", "first"] if failure == "missing" else ["second", "first"])
    assert "fixture-secret" not in str(builder._logger.mock_calls)


@pytest.mark.asyncio
async def test_empty_store_discovery_is_not_success(monkeypatch):
    builder = AgentBuilder(ToolsRuntimeSettings())
    monkeypatch.setattr(builder, "_build_store_tools_from_config", Mock(return_value=[]))
    monkeypatch.setattr(builder, "_fetch_mcp_tools_from_endpoint", AsyncMock(return_value=[]))
    tool = binding().model_copy(update={"tool_source_type": "STORE"})
    with pytest.raises(MCPToolError, match="已绑定的工具暂时不可用"):
        await builder._build_mcp_server_instances({tool.endpoint: [tool]}, {"X-API-Key": "fixture-only"})


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_runtime_reports_error_without_starting_the_model(monkeypatch, stream):
    runtime = ToolsRuntimeService(ToolsRuntimeSettings())
    builder = runtime._builder
    real_build = builder.build_agent

    async def build_with_binding(request):
        return await real_build(request, SimpleNamespace(tools=[binding()]))

    monkeypatch.setattr(builder, "build_agent", build_with_binding)
    monkeypatch.setattr(builder, "_build_rag_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_workflow_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_mcp_tools_from_agent", AsyncMock(side_effect=OSError("fixture-secret")))
    model = Mock(side_effect=AssertionError("model must not run without the bound tool"))
    monkeypatch.setattr(builder, "_initialize_model", model)
    request = AgentRunRequest(message="查一下物流", skills_enabled=False)
    if stream:
        events = [event async for event in runtime.stream_agent(request)]
        assert [event.event for event in events] == ["error"]
        assert events[0].error_type == "MCPToolError"
        assert "已绑定的工具暂时不可用" in events[0].error
        assert "fixture-secret" not in events[0].error
    else:
        result = await runtime.run_agent(request)
        assert not result.success
        assert "已绑定的工具暂时不可用" in result.error
        assert "fixture-secret" not in result.error
    model.assert_not_called()
