"""Persisted HTTP/plugin bindings must not vanish during agent setup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models.internal import AgentTool
from app.runtime.core.exceptions import MCPToolError
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.executor.service import ToolsRuntimeService
from app.runtime.tools.models import AgentRunRequest


def binding(kind: str) -> AgentTool:
    return AgentTool(
        tool_id=uuid4(),
        tool_name="query",
        tool_type="MCP",
        enabled=True,
        transport_type=kind,
        endpoint="https://fixture.invalid/query" if kind == "http_webhook" else None,
        base_config={"plugin_id": "fixture", "tool_name": "query"}
        if kind == "plugin"
        else {},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["http_webhook", "plugin"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", ["factory", "missing_config"])
async def test_bound_failure_stops_real_runtime_before_model(
    monkeypatch,
    kind,
    stream,
    failure,
):
    runtime = ToolsRuntimeService(ToolsRuntimeSettings())
    builder = runtime._builder
    tool = binding(kind)
    if failure == "missing_config":
        tool = tool.model_copy(update={"endpoint": None, "base_config": {}})
    internal = SimpleNamespace(id=uuid4(), tools=[tool])
    real_build = builder.build_agent

    async def build_with_binding(request):
        return await real_build(request, internal)

    monkeypatch.setattr(builder, "build_agent", build_with_binding)
    monkeypatch.setattr(builder, "_build_rag_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_workflow_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_auth_headers", AsyncMock(return_value={}))
    factory = Mock(side_effect=ValueError("Authorization: Bearer fixture-secret"))
    factory_name = (
        "create_http_tool" if kind == "http_webhook" else "create_plugin_tool"
    )
    monkeypatch.setattr(
        f"app.runtime.tools.builder.agent_builder.{factory_name}", factory
    )
    model = Mock(side_effect=AssertionError("model must not start"))
    monkeypatch.setattr(builder, "_initialize_model", model)
    builder._logger = Mock()

    request = AgentRunRequest(message="查一下物流", skills_enabled=False)
    if stream:
        events = [event async for event in runtime.stream_agent(request)]
        assert [event.event for event in events] == ["error"]
        assert events[0].error_type == "MCPToolError"
        error = events[0].error
    else:
        result = await runtime.run_agent(request)
        assert not result.success
        error = result.error
    assert "已绑定的工具暂时不可用" in error
    assert "fixture-secret" not in error
    assert "fixture-secret" not in str(builder._logger.mock_calls)
    model.assert_not_called()
    if failure == "factory":
        factory.assert_called_once()
    else:
        factory.assert_not_called()


@pytest.mark.parametrize("kind", ["http_webhook", "plugin"])
def test_failure_does_not_return_a_partially_built_tool_list(monkeypatch, kind):
    builder = AgentBuilder(ToolsRuntimeSettings())
    factory = Mock(side_effect=[object(), ValueError("fixture-secret")])
    factory_name = (
        "create_http_tool" if kind == "http_webhook" else "create_plugin_tool"
    )
    monkeypatch.setattr(
        f"app.runtime.tools.builder.agent_builder.{factory_name}", factory
    )
    with pytest.raises(MCPToolError, match="已绑定的工具暂时不可用"):
        if kind == "plugin":
            builder._build_plugin_tools(
                [binding(kind), binding(kind)], None, None, "agent", "project"
            )
        else:
            builder._build_http_webhook_tools([binding(kind), binding(kind)])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["http_webhook", "plugin"])
async def test_disabled_broken_binding_is_still_ignored(monkeypatch, kind):
    builder = AgentBuilder(ToolsRuntimeSettings())
    tool = binding(kind).model_copy(
        update={"enabled": False, "endpoint": None, "base_config": {}}
    )
    factory = Mock(side_effect=AssertionError("disabled tool must not be built"))
    factory_name = (
        "create_http_tool" if kind == "http_webhook" else "create_plugin_tool"
    )
    monkeypatch.setattr(
        f"app.runtime.tools.builder.agent_builder.{factory_name}", factory
    )
    assert (
        await builder._build_mcp_tools_from_agent(
            SimpleNamespace(id=uuid4(), tools=[tool]),
            None,
            None,
        )
        == []
    )
    factory.assert_not_called()
