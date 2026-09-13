"""Saved MCP configuration must agree between direct tests and agent runtime."""

import json
import socket
import time
from threading import Thread
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from starlette.responses import Response
from agno.tools.function import FunctionCall
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import tools
from app.database import get_db
from app.dependencies import get_current_or_internal_project_id
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.core.exceptions import MCPToolError
from app.schemas.agent import AgentCreate, AgentToolCreate, AgentUpdate
from app.services.agent_service import AgentService
from app.models.internal import AgentTool


@pytest.fixture
def mcp_targets():
    running = []
    calls = []

    def start(version, transport, protected=False):
        mcp = FastMCP(
            name=f"fixture-{version}", streamable_http_path="/fixture-rpc",
            sse_path="/fixture-events", json_response=True,
        )

        @mcp.tool()
        def query(tracking_no: str) -> dict[str, str]:
            """Return an isolated fixture result, not business data."""
            calls.append((version, tracking_no))
            return {"version": version, "tracking_no": tracking_no}

        @mcp.tool()
        def unbound_operation() -> str:
            """Must not become available simply by connecting to this server."""
            pytest.fail("Unbound tool was executed")

        application = mcp.streamable_http_app() if transport == "http" else mcp.sse_app()

        async def authenticated(scope, receive, send):
            if protected and scope["type"] == "http":
                if dict(scope["headers"]).get(b"authorization") != f"Bearer {version}".encode():
                    await Response(status_code=401)(scope, receive, send)
                    return
            await application(scope, receive, send)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(
            authenticated, log_level="error", access_log=False, timeout_graceful_shutdown=2,
        ))
        thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        running.append((server, thread, listener))
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "Isolated MCP fixture did not start"
        path = "/fixture-rpc" if transport == "http" else "/fixture-events"
        return f"http://127.0.0.1:{listener.getsockname()[1]}{path}"

    try:
        yield start, calls
    finally:
        for server, thread, listener in reversed(running):
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()
            assert not thread.is_alive(), "MCP fixture must not survive the test"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
@pytest.mark.parametrize("protected", [False, True])
async def test_mcp_saved_endpoint_matches_direct_and_agent_execution(db_session, mcp_targets, transport, protected):
    start, calls = mcp_targets
    first_url = start("original", transport, protected)
    edited_url = start("edited", transport, protected)
    project_id = uuid4()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def session_dependency():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(tools.router)
    app.dependency_overrides[get_db] = session_dependency
    app.dependency_overrides[get_current_or_internal_project_id] = lambda: project_id
    builder = AgentBuilder(ToolsRuntimeSettings())

    async def runtime(agent_id, tracking_no=None):
        async with factory() as session:
            agent = await AIServiceClient(AgentService(session), project_id).get_agent(str(agent_id), {})
        instances = await builder._build_mcp_tools_from_agent(agent, None, None)
        try:
            if tracking_no:
                assert len(instances) == 1
                assert set(instances[0].functions) == {"query"}
                result = await instances[0].functions["query"].entrypoint(tracking_no=tracking_no)
                return json.loads(result.content)
            return len(instances)
        finally:
            for instance in reversed(instances):
                await instance.close()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/tools", json={
            "project_id": str(project_id), "name": "query", "tool_type": "MCP",
            "transport_type": transport, "endpoint": first_url,
            "config": {"headers": {"Authorization": "Bearer original"}} if protected else {},
        })
        assert created.status_code == 200
        tool_id = UUID(created.json()["id"])
        async with factory() as session:
            agent = await AgentService(session).create_agent(project_id, AgentCreate(
                name="Isolated MCP employee", model="fixture-model",
                tools=[AgentToolCreate(tool_id=tool_id)],
            ))
            agent_id = agent.id
        assert await runtime(agent_id, "AGENT-BEFORE") == {
            "version": "original", "tracking_no": "AGENT-BEFORE",
        }
        direct = await client.post(f"/tools/{tool_id}/execute", json={
            "input_data": {"tracking_no": "DIRECT-BEFORE"},
        })
        assert direct.status_code == 200
        assert direct.json()["success"] is True, direct.json()
        assert direct.json()["output_data"]["version"] == "original"
        changed = await client.patch(f"/tools/{tool_id}", params={"project_id": str(project_id)}, json={
            "endpoint": edited_url,
            "config": {"headers": {"Authorization": "Bearer edited"}} if protected else {},
        })
        assert changed.status_code == 200
        assert await runtime(agent_id, "AGENT-AFTER") == {
            "version": "edited", "tracking_no": "AGENT-AFTER",
        }
        direct = await client.post(f"/tools/{tool_id}/execute", json={
            "input_data": {"tracking_no": "DIRECT-AFTER"},
        })
        assert direct.json()["success"] is True, direct.json()
        assert direct.json()["output_data"]["version"] == "edited"
        if protected:
            # Wrong or removed credentials must not invoke the business tool.
            for invalid_headers in ({"Authorization": "Bearer wrong"}, {}):
                changed = await client.patch(f"/tools/{tool_id}", params={"project_id": str(project_id)}, json={
                    "config": {"headers": invalid_headers},
                })
                assert changed.status_code == 200
                denied = await client.post(f"/tools/{tool_id}/execute", json={"input_data": {"tracking_no": "DENIED"}})
                assert denied.json()["success"] is False
                with pytest.raises(MCPToolError, match="已绑定的工具暂时不可用"):
                    await runtime(agent_id)
            await client.patch(f"/tools/{tool_id}", params={"project_id": str(project_id)}, json={
                "config": {"headers": {"Authorization": "Bearer edited"}},
            })
        async with factory() as session:
            await AgentService(session).set_tool_enabled(project_id, agent_id, tool_id, False)
        assert await runtime(agent_id) == 0
        async with factory() as session:
            await AgentService(session).set_tool_enabled(project_id, agent_id, tool_id, True)
        assert await runtime(agent_id) == 1
        async with factory() as session:
            await AgentService(session).update_agent(project_id, agent_id, AgentUpdate(tools=[]))
        assert await runtime(agent_id) == 0
        async with factory() as session:
            await AgentService(session).update_agent(
                project_id, agent_id, AgentUpdate(tools=[AgentToolCreate(tool_id=tool_id)]),
            )
        removed = await client.delete(f"/tools/{tool_id}", params={"project_id": str(project_id)})
        assert removed.status_code == 200
        assert await runtime(agent_id) == 0
        assert (await client.post(f"/tools/{tool_id}/execute", json={"input_data": {}})).status_code == 404
        assert calls == [
            ("original", "AGENT-BEFORE"), ("original", "DIRECT-BEFORE"),
            ("edited", "AGENT-AFTER"), ("edited", "DIRECT-AFTER"),
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_partial_real_connections_are_closed_before_retry(mcp_targets, transport):
    start, calls = mcp_targets
    endpoints = [start("one", transport), start("two", transport), start("denied", transport, True)]
    bindings = {
        endpoint: [AgentTool(tool_id=uuid4(), tool_name="query", tool_type="MCP",
                             transport_type=transport, endpoint=endpoint)]
        for endpoint in endpoints
    }
    builder = AgentBuilder(ToolsRuntimeSettings())
    with pytest.raises(MCPToolError, match="已绑定的工具暂时不可用"):
        await builder._build_mcp_server_instances(bindings, {})
    # A failed third server must not poison the task's AnyIO scope stack.
    instances, _ = await builder._build_mcp_server_instances({endpoints[0]: bindings[endpoints[0]]}, {})
    try:
        result = await instances[0].functions["query"].entrypoint(tracking_no="AFTER-FAILURE")
        assert json.loads(result.content)["version"] == "one"
    finally:
        await instances[0].close()
    assert calls == [("one", "AFTER-FAILURE")]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_saved_mcp_reports_protocol_errors_and_preserves_rich_results(monkeypatch, failed):
    from unittest.mock import AsyncMock
    from mcp.types import CallToolResult, ImageContent, Tool
    from app.runtime.tools.saved_mcp import SavedMCPTools
    from agno.tools.function import Function

    toolkit = SavedMCPTools("http://fixture.test/mcp", "http", {}, ["query"])
    reply = CallToolResult(isError=failed, structuredContent={"status": "fixture"}, content=[
        ImageContent(type="image", data="aW1hZ2U=", mimeType="image/png"),
    ])
    session = AsyncMock()
    session.call_tool.return_value = reply
    session.list_tools.return_value = type("Tools", (), {"tools": [Tool(name="query", inputSchema={"type": "object"})]})()
    toolkit.session = session
    await toolkit.build_tools()
    assert isinstance(toolkit.functions["query"], Function)
    execution = await FunctionCall(function=toolkit.functions["query"], arguments={}).aexecute()
    assert execution.status == ("failure" if failed else "success")
    session.call_tool.assert_awaited_once()
    if not failed:
        assert execution.result.content == '{"status": "fixture"}'
        assert execution.result.images and execution.result.images[0].content == b"image"
