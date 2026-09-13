"""Persisted tool fields and association lifecycle must survive runtime mapping."""

from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, Mock
from typing import cast
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent as DBAgent
from app.models.tool import Tool, ToolType
from app.runtime.supervisor.infrastructure.services import _convert_agent
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.services.agent_service import AgentService


def bound_agent() -> tuple[DBAgent, Tool]:
    now = datetime.now(timezone.utc)
    project_id = uuid4()
    tool = Tool(
        id=uuid4(),
        project_id=project_id,
        name="parcel_lookup",
        description="根据运单号查询包裹运输轨迹，不查询商品价格。",
        tool_type=ToolType.FUNCTION,
        transport_type="http_webhook",
        endpoint="https://example.test/parcel",
        deleted_at=None,
        created_at=now,
        updated_at=now,
        config={
            "method": "POST",
            "timeout": 5,
            "headers": {"X-Fixture": "not-a-secret"},
            "parameters": [
                {
                    "name": "tracking_no",
                    "type": "string",
                    "required": True,
                    "description": "运单号",
                }
            ],
        },
    )
    agent = DBAgent(
        id=uuid4(),
        project_id=project_id,
        name="验收专员",
        model="fixture",
        instruction="测试",
        config={},
        tools=[tool],
        collections=[],
        workflows=[],
        is_default=False,
        is_remote_store_agent=False,
        skills_enabled=True,
        created_at=now,
        updated_at=now,
    )
    return agent, tool


def test_deleted_tools_do_not_reenter_the_agent_runtime() -> None:
    agent, tool = bound_agent()
    tool.deleted_at = datetime.now(timezone.utc)
    assert _convert_agent(agent, {tool.id: {"enabled": True}}).tools == []


def test_removed_binding_is_not_defaulted_back_to_enabled() -> None:
    agent, _tool = bound_agent()
    assert _convert_agent(agent, {}).tools == []


def test_foreign_project_tool_is_not_loaded_from_a_stale_relationship() -> None:
    agent, tool = bound_agent()
    tool.project_id = uuid4()
    assert _convert_agent(agent, {tool.id: {"enabled": True}}).tools == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "deleted", "unbound", "foreign"])
async def test_employee_details_only_advertise_current_project_bindings(
    state: str,
) -> None:
    agent, tool = bound_agent()
    if state == "deleted":
        tool.deleted_at = datetime.now(timezone.utc)
    if state == "foreign":
        tool.project_id = uuid4()
    rows = [] if state == "unbound" else [(agent.id, tool.id, True, [], {})]
    database = Mock(execute=AsyncMock(return_value=Mock(all=lambda: rows)))
    await AgentService(cast(AsyncSession, database)).enrich_agents_with_tool_details(
        [agent],
        agent.project_id,
    )
    assert len(agent._tools_data) == (1 if state == "active" else 0)


@pytest.mark.asyncio
async def test_bound_tool_exposes_description_and_parameters_to_model(
    monkeypatch,
) -> None:
    agent, tool = bound_agent()
    internal = _convert_agent(agent, {tool.id: {"enabled": True}})
    builder = AgentBuilder(ToolsRuntimeSettings())
    monkeypatch.setattr(builder, "_build_auth_headers", AsyncMock(return_value={}))
    functions = await builder._build_mcp_tools_from_agent(internal, None, None)
    assert len(functions) == 1
    function = functions[0]
    assert function.name == tool.name
    assert function.description == tool.description
    assert function.parameters["required"] == ["tracking_no"]
    assert function.parameters["properties"]["tracking_no"]["description"] == "运单号"


@pytest.mark.asyncio
async def test_disabled_binding_never_builds_a_callable(monkeypatch) -> None:
    agent, tool = bound_agent()
    internal = _convert_agent(agent, {tool.id: {"enabled": False}})
    builder = AgentBuilder(ToolsRuntimeSettings())
    authentication = AsyncMock(return_value={})
    monkeypatch.setattr(builder, "_build_auth_headers", authentication)
    assert await builder._build_mcp_tools_from_agent(internal, None, None) == []
    authentication.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 401, 500])
async def test_bound_http_function_uses_saved_transport_and_reports_failures(
    monkeypatch, status
) -> None:
    agent, tool = bound_agent()
    internal = _convert_agent(agent, {tool.id: {"enabled": True}})
    function = AgentBuilder(ToolsRuntimeSettings())._build_http_webhook_tools(
        internal.tools
    )[0]
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={"fixture_result": "in_transit"})

    actual_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: actual_client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    result = await function.entrypoint(tracking_no="TEST-0001")
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == tool.endpoint
    assert requests[0].headers["X-Fixture"] == "not-a-secret"
    assert b"TEST-0001" in requests[0].content
    assert result.startswith("<error>") is (status != 200)
    if status != 200:
        assert str(status) in result


@pytest.mark.asyncio
async def test_runtime_calls_local_http_server_and_observes_saved_changes() -> None:
    requests: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.reply(payload["tracking_no"])

        def do_GET(self) -> None:
            self.reply(parse_qs(urlparse(self.path).query)["tracking_no"][0])

        def reply(self, tracking_no: str) -> None:
            requests.append((self.command, urlparse(self.path).path))
            output = json.dumps(
                {"tracking_no": tracking_no, "status": "fixture_transit"}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(
        target=lambda: server.serve_forever(poll_interval=0.05), daemon=True
    )
    thread.start()
    try:
        agent, tool = bound_agent()
        tool.endpoint = f"http://127.0.0.1:{server.server_port}/original"
        builder = AgentBuilder(ToolsRuntimeSettings())
        for path, method in [("original", "POST"), ("edited", "GET")]:
            tool.endpoint = f"http://127.0.0.1:{server.server_port}/{path}"
            tool.config = {**tool.config, "method": method}
            internal = _convert_agent(agent, {tool.id: {"enabled": True}})
            function = builder._build_http_webhook_tools(internal.tools)[0]
            output = await function.entrypoint(tracking_no="TEST-LOCAL-001")
            assert json.loads(output) == {
                "tracking_no": "TEST-LOCAL-001",
                "status": "fixture_transit",
            }
        assert requests == [("POST", "/original"), ("GET", "/edited")]
        tool.deleted_at = datetime.now(timezone.utc)
        internal = _convert_agent(agent, {tool.id: {"enabled": True}})
        assert builder._build_http_webhook_tools(internal.tools) == []
        assert len(requests) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
