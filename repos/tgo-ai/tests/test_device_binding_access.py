"""Device binding validation must precede persistence and survive long tool runs."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from app.exceptions import NotFoundError
from app.runtime.tools.utils import create_agno_mcp_tool
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import AgentConfig
from app.runtime.core.exceptions import MCPConnectionError
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services import agent_service as service_module
from app.services.agent_service import AgentService


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_foreign_binding_is_rejected_before_any_write(monkeypatch, operation):
    db = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    validate = AsyncMock(side_effect=NotFoundError("Device", "not-owned"))
    client = getattr(service_module, "device_control_client", SimpleNamespace())
    monkeypatch.setattr(client, "validate_binding", validate, raising=False)
    monkeypatch.setattr(service_module, "device_control_client", client, raising=False)
    service = AgentService(db)
    existing = SimpleNamespace(id=uuid4(), bound_device_id="old-binding", name="existing")
    monkeypatch.setattr(service, "get_agent", AsyncMock(return_value=existing))
    project, device = uuid4(), str(uuid4())
    with pytest.raises(NotFoundError):
        if operation == "create":
            await service.create_agent(project, AgentCreate(name="Fixture", model="fixture", bound_device_id=device))
        else:
            await service.update_agent(project, existing.id, AgentUpdate(bound_device_id=device, name="changed"))
    validate.assert_awaited_once_with(project, device)
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()
    assert existing.bound_device_id == "old-binding" and existing.name == "existing"


@pytest.mark.asyncio
async def test_unbinding_does_not_require_device_service(monkeypatch):
    db = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    service = AgentService(db)
    existing = SimpleNamespace(id=uuid4(), bound_device_id=str(uuid4()))
    monkeypatch.setattr(service, "get_agent", AsyncMock(return_value=existing))
    changed = await service.update_agent(uuid4(), existing.id, AgentUpdate(bound_device_id=None))
    assert changed.bound_device_id is None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_refreshes_signed_headers_on_each_tool_call(monkeypatch):
    from app.runtime.tools import utils

    seen = []

    @asynccontextmanager
    async def transport(url, headers):
        seen.append(headers)
        yield None, None, None

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def initialize(self):
            return None

        async def call_tool(self, *args, **kwargs):
            return CallToolResult(content=[TextContent(type="text", text="fixture")])

    monkeypatch.setattr(utils, "streamablehttp_client", transport)
    monkeypatch.setattr(utils, "ClientSession", Session)
    monkeypatch.setattr(utils, "_record_tool_usage", AsyncMock())
    counter = iter(("first", "second"))
    function = create_agno_mcp_tool(
        Tool(name="fixture", inputSchema={"type": "object"}),
        "http://fixture/mcp",
        headers_factory=lambda: {"Authorization": next(counter)},
    )
    assert await function.entrypoint() == "fixture"
    assert await function.entrypoint() == "fixture"
    assert seen == [{"Authorization": "first"}, {"Authorization": "second"}]


@pytest.mark.asyncio
async def test_bound_device_failure_cannot_silently_remove_tools(monkeypatch):
    builder = AgentBuilder(ToolsRuntimeSettings())
    monkeypatch.setattr(builder, "_build_rag_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builder, "_build_workflow_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        builder, "_build_device_mcp_tools", AsyncMock(side_effect=MCPConnectionError("device unavailable"))
    )
    agent = SimpleNamespace(tools=[], bound_device_id=str(uuid4()))
    with pytest.raises(MCPConnectionError, match="device unavailable"):
        await builder._build_tools(AgentConfig(), None, None, internal_agent=agent)


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_unrelated_mcp_binding_does_not_filter_bound_device_tools(monkeypatch, empty):
    from app.runtime.tools.builder import agent_builder as module
    from app.config import settings
    from jose import jwt

    seen = []

    @asynccontextmanager
    async def transport(url, headers):
        seen.append(headers)
        yield None, None, None

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def initialize(self):
            return None

        async def list_tools(self, **kwargs):
            return ListToolsResult(tools=[] if empty else [Tool(name="fixture_echo", inputSchema={"type": "object"})])

    monkeypatch.setattr(module, "streamablehttp_client", transport)
    monkeypatch.setattr(module, "ClientSession", Session)
    project, device = str(uuid4()), str(uuid4())
    agent = SimpleNamespace(
        id=uuid4(),
        project_id=project,
        bound_device_id=device,
        tools=[SimpleNamespace(tool_type="MCP", tool_name="parcel_lookup", enabled=True)],
    )
    if empty:
        with pytest.raises(MCPConnectionError, match="绑定设备"):
            await AgentBuilder(ToolsRuntimeSettings())._build_device_mcp_tools(agent)
    else:
        tools = await AgentBuilder(ToolsRuntimeSettings())._build_device_mcp_tools(agent)
        assert [tool.name for tool in tools] == ["fixture_echo"]
    claims = jwt.decode(
        seen[0]["Authorization"][7:],
        settings.secret_key,
        algorithms=["HS256"],
        audience="tgo-device-control",
        issuer="tgo-internal",
    )
    assert claims["project_id"] == project and claims["device_id"] == device
