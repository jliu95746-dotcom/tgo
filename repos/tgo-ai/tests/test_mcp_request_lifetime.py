"""Each runtime request owns the connections it opens, including build errors."""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agno.agent import RunCompletedEvent, RunContentEvent, RunOutput

from app.runtime.core.exceptions import AgentExecutionError, InvalidConfigurationError, StreamingError
from app.runtime.tools.executor.service import ToolsRuntimeService
from app.runtime.tools.models import AgentRunRequest
from app.runtime.tools.saved_mcp import SavedMCPTools


@pytest.fixture
def toolkits(monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    fixture = Path(__file__).parent / "fixtures" / "stdio_tool_server.py"
    return [SavedMCPTools(f'python "{fixture.as_posix()}" {version}', "stdio", {}, ["query"])
            for version in ["first", "second"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel", "build_error"])
async def test_tool_runtime_releases_real_connections(monkeypatch, toolkits, stream, outcome):
    runtime = ToolsRuntimeService()

    def failure():
        if outcome == "error":
            raise ValueError("fixture-error")
        if outcome == "cancel":
            raise asyncio.CancelledError()

    async def ordinary(*args, **kwargs):
        failure()
        return RunOutput(content="done")

    async def streaming(*args, **kwargs):
        yield RunContentEvent(content="part")
        failure()
        yield RunCompletedEvent(content="done")

    async def build(request):
        for toolkit in toolkits:
            await toolkit.connect()
        if outcome == "build_error":
            raise InvalidConfigurationError("fixture-build-error")
        return SimpleNamespace(arun=streaming if stream else ordinary)

    monkeypatch.setattr(runtime._builder, "build_agent", build)
    request = AgentRunRequest(message="fixture")

    async def invoke():
        if stream:
            return [event async for event in runtime.stream_agent(request)]
        return await runtime.run_agent(request)

    try:
        if outcome == "error":
            with pytest.raises(StreamingError if stream else AgentExecutionError):
                await invoke()
        elif outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await invoke()
        else:
            await invoke()
        assert all(toolkit.session is None for toolkit in toolkits)
        assert all(not toolkit.initialized for toolkit in toolkits)
    finally:
        for toolkit in reversed(toolkits):
            await toolkit.close()


@pytest.mark.asyncio
async def test_consumer_closing_stream_closes_inner_generator_first(monkeypatch, toolkits):
    runtime = ToolsRuntimeService()
    released = []

    async def arun(*args, **kwargs):
        try:
            yield RunContentEvent(content="part")
            yield RunCompletedEvent(content="done")
        finally:
            assert all(toolkit.initialized for toolkit in toolkits)
            released.append("generator")

    async def build(request):
        for toolkit in toolkits:
            await toolkit.connect()
        return SimpleNamespace(arun=arun)

    monkeypatch.setattr(runtime._builder, "build_agent", build)
    stream = runtime.stream_agent(AgentRunRequest(message="fixture"))
    try:
        assert (await anext(stream)).event == "content"
        await stream.aclose()
        assert released == ["generator"]
        assert all(toolkit.session is None for toolkit in toolkits)
    finally:
        await stream.aclose()
        for toolkit in reversed(toolkits):
            await toolkit.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_main_customer_stream_releases_real_connections(monkeypatch, toolkits, cancel):
    from tests.test_supervisor_cancellation import make_execution, dispose, domain_events
    ready, release = asyncio.Event(), asyncio.Event()

    async def arun(*args, **kwargs):
        for toolkit in toolkits:
            await toolkit.connect()
        ready.set()
        yield RunContentEvent(content="part")
        await release.wait()
        yield RunCompletedEvent(content="done")

    execution = await make_execution(monkeypatch, "plain", real_agent=SimpleNamespace(arun=arun))
    try:
        await asyncio.wait_for(ready.wait(), 3)
        if cancel:
            assert await execution.runtime.cancel(execution.state.run_id, execution.project)
        else:
            release.set()
        await domain_events(execution.response)
        await execution.state.task
        assert all(toolkit.session is None for toolkit in toolkits)
    finally:
        release.set()
        await dispose(execution)


@pytest.mark.asyncio
@pytest.mark.parametrize("build_error", [False, True])
async def test_main_customer_nonstream_owns_build_and_run(monkeypatch, toolkits, build_error):
    from uuid import uuid4
    from app.runtime.supervisor.application.service import SupervisorRuntimeService
    from app.schemas.agent_run import SupervisorRunRequest, SupervisorRunResponse
    from unittest.mock import AsyncMock

    project = uuid4()
    runtime = SupervisorRuntimeService(session_factory=Mock(), tools_runtime_service=Mock(_settings=None))
    context = SimpleNamespace(disable_tools=False, response_purpose="standard", request_id="fixture",
                              agent=SimpleNamespace(id=uuid4(), bound_device_id=None))
    runtime._prepare_context = AsyncMock(return_value=(context, "fixture"))

    async def build(ctx):
        for toolkit in toolkits:
            await toolkit.connect()
        if build_error:
            raise ValueError("fixture-build-error")
        return SimpleNamespace()

    runtime._agent_builder.build_agent = build
    runtime._agent_runner.run = AsyncMock(return_value=SupervisorRunResponse(success=True, message="done", content="done"))
    try:
        result = await runtime.run(SupervisorRunRequest(message="fixture"), project)
        assert result.success is (not build_error)
        assert all(toolkit.session is None for toolkit in toolkits)
    finally:
        for toolkit in reversed(toolkits):
            await toolkit.close()


@pytest.mark.asyncio
async def test_real_builder_model_failure_releases_preconnected_tool(monkeypatch, toolkits):
    from uuid import uuid4
    from unittest.mock import AsyncMock
    from app.models.internal import AgentTool

    runtime = ToolsRuntimeService()
    builder = runtime._builder
    real_build, real_connect = builder.build_agent, SavedMCPTools.connect
    connected = []
    tool = AgentTool(tool_id=uuid4(), tool_name="query", tool_type="MCP", transport_type="stdio",
                     endpoint=toolkits[0]._saved_url)

    async def connect(self, force=False):
        await real_connect(self, force)
        connected.append(self)

    async def build(request):
        return await real_build(request, SimpleNamespace(id=uuid4(), tools=[tool]))

    monkeypatch.setattr(SavedMCPTools, "connect", connect)
    monkeypatch.setattr(builder, "build_agent", build)
    monkeypatch.setattr(builder, "_build_auth_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(builder, "_build_rag_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_workflow_tools", AsyncMock(return_value=[]))
    model = Mock(side_effect=InvalidConfigurationError("fixture-model-error"))
    monkeypatch.setattr(builder, "_initialize_model", model)
    try:
        result = await runtime.run_agent(AgentRunRequest(message="fixture", skills_enabled=False))
        assert not result.success
        model.assert_called_once()
        assert len(connected) == 1
        assert connected[0].session is None
    finally:
        for toolkit in reversed(connected):
            await toolkit.close()
