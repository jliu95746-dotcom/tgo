"""Exercise saved stdio bindings through a real isolated child process."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from agno.tools.function import FunctionCall

from app.models.internal import AgentTool
from app.runtime.core.exceptions import MCPToolError
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.executor.service import ToolsRuntimeService
from app.runtime.tools.models import AgentRunRequest
from app.runtime.tools.saved_mcp import SavedMCPTools


def binding(command: str) -> AgentTool:
    return AgentTool(tool_id=uuid4(), tool_name="query", tool_type="MCP",
                     transport_type="stdio", endpoint=command, enabled=True)


@pytest.fixture
def command(monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    fixture = Path(__file__).parent / "fixtures" / "stdio_tool_server.py"
    return f'python "{fixture.as_posix()}"'


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["original", "edited", "error"])
async def test_real_stdio_only_exposes_bound_tool_and_preserves_call_status(command, version):
    builder = AgentBuilder(ToolsRuntimeSettings())
    endpoint = f"{command} {version}"
    instances, deferred = await builder._build_mcp_server_instances({endpoint: [binding(endpoint)]}, {})
    try:
        assert deferred == [], "stdio must connect and validate before model initialization"
        assert len(instances) == 1
        toolkit = instances[0]
        assert toolkit.initialized
        assert set(toolkit.functions) == {"query"}
        execution = await FunctionCall(function=toolkit.functions["query"], arguments={"tracking_no": "fixture"}).aexecute()
        assert execution.status == ("failure" if version == "error" else "success")
        if version != "error":
            assert json.loads(execution.result.content) == {"version": version, "tracking_no": "fixture"}
    finally:
        for instance in reversed(instances):
            await instance.close()
    assert toolkit.session is None
    assert not toolkit.initialized


@pytest.mark.asyncio
async def test_missing_bound_tool_closes_real_children_before_retry(command, monkeypatch):
    from mcp.client.stdio import stdio_client
    active = []

    @asynccontextmanager
    async def tracked_transport(params):
        async with stdio_client(params) as streams:
            active.append(params.args[-1])
            try:
                yield streams
            finally:
                active.pop()

    monkeypatch.setattr("app.runtime.tools.saved_mcp.stdio_client", tracked_transport)
    builder = AgentBuilder(ToolsRuntimeSettings())
    endpoints = [f"{command} {version}" for version in ["original", "missing"]]
    with pytest.raises(MCPToolError, match="已绑定的工具暂时不可用"):
        await builder._build_mcp_server_instances({endpoint: [binding(endpoint)] for endpoint in endpoints}, {})
    assert active == []
    toolkit = SavedMCPTools(endpoints[0], "stdio", {}, ["query"])
    await toolkit.connect()
    try:
        assert set(toolkit.functions) == {"query"}
    finally:
        await toolkit.close()
    assert active == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_failed_stdio_setup_stops_runtime_before_model(monkeypatch, stream):
    runtime = ToolsRuntimeService(ToolsRuntimeSettings())
    builder = runtime._builder
    real_build = builder.build_agent
    internal = SimpleNamespace(id=uuid4(), tools=[binding("python fixture")])

    async def with_binding(request):
        return await real_build(request, internal)

    monkeypatch.setattr(builder, "build_agent", with_binding)
    monkeypatch.setattr(builder, "_build_rag_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_workflow_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_auth_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(SavedMCPTools, "connect", AsyncMock(side_effect=OSError("fixture-secret")))
    model = Mock(side_effect=AssertionError("model must not start"))
    monkeypatch.setattr(builder, "_initialize_model", model)
    request = AgentRunRequest(message="查询", skills_enabled=False)
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
    model.assert_not_called()


@pytest.mark.asyncio
async def test_stdio_cancellation_closes_transport_and_propagates(monkeypatch):
    closed = []

    @asynccontextmanager
    async def transport(params):
        try:
            yield None, None
        finally:
            closed.append("transport")

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.initialize.side_effect = asyncio.CancelledError()
    monkeypatch.setattr("app.runtime.tools.saved_mcp.stdio_client", transport)
    monkeypatch.setattr("app.runtime.tools.saved_mcp.ClientSession", Mock(return_value=session))
    toolkit = SavedMCPTools("python fixture", "stdio", {}, ["query"])
    with pytest.raises(asyncio.CancelledError):
        await toolkit.connect()
    assert closed == ["transport"]
    session.__aexit__.assert_awaited_once()
    assert toolkit.session is None
    assert not toolkit.initialized


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [{"endpoint": None}, {"transport_type": "unknown"}])
async def test_incomplete_mcp_binding_is_not_silently_skipped(monkeypatch, update):
    builder = AgentBuilder(ToolsRuntimeSettings())
    monkeypatch.setattr(builder, "_build_auth_headers", AsyncMock(return_value={}))
    internal = SimpleNamespace(id=uuid4(), tools=[binding("python fixture").model_copy(update=update)])
    with pytest.raises(MCPToolError):
        await builder._build_mcp_tools_from_agent(internal, None, None)


@pytest.mark.asyncio
async def test_disabled_stdio_binding_does_not_launch_a_process(monkeypatch):
    builder = AgentBuilder(ToolsRuntimeSettings())
    connect = AsyncMock(side_effect=AssertionError("disabled tool must not connect"))
    monkeypatch.setattr(SavedMCPTools, "connect", connect)
    tool = binding("python fixture").model_copy(update={"enabled": False})
    assert await builder._build_mcp_tools_from_agent(SimpleNamespace(id=uuid4(), tools=[tool]), None, None) == []
    connect.assert_not_awaited()
