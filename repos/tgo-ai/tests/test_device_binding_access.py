"""Device binding validation must precede persistence and survive long tool runs."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from app.exceptions import ConflictError, NotFoundError
from app.runtime.tools.utils import create_agno_mcp_tool
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import AgentConfig
from app.runtime.core.exceptions import MCPConnectionError
from app.runtime.core.exceptions import MCPToolError
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services import agent_service as service_module
from app.services.agent_service import AgentService


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_foreign_binding_is_rejected_before_any_write(monkeypatch, operation):
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
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
async def test_unbinding_does_not_require_device_service(monkeypatch, db_session, test_project):
    from app.models.agent import Agent
    validate = AsyncMock()
    monkeypatch.setattr(service_module.device_control_client, "validate_binding", validate)
    service = AgentService(db_session)
    existing = Agent(id=uuid4(), project_id=test_project.id, name="隔离设备员工", model="fixture",
                     is_active=False, bound_device_id=str(uuid4()))
    db_session.add(existing)
    await db_session.commit()
    changed = await service.update_agent(test_project.id, existing.id, AgentUpdate(bound_device_id=None),
                                         enrich_resources=False)
    assert changed.bound_device_id is None
    validate.assert_not_awaited()
    await db_session.refresh(existing)
    assert existing.bound_device_id is None


@pytest.mark.asyncio
async def test_employee_binding_pins_verified_device_policy(monkeypatch, db_session, test_project):
    policy = SimpleNamespace(digest="a" * 64)
    validate = AsyncMock(return_value=policy)
    monkeypatch.setattr(service_module.device_control_client, "validate_binding", validate)
    service = AgentService(db_session)
    device_id = str(uuid4())
    agent = await service.create_agent(
        test_project.id,
        AgentCreate(name="只读设备员工", model="fixture", bound_device_id=device_id),
    )
    assert agent.bound_device_policy_digest == policy.digest
    validate.assert_awaited_once_with(test_project.id, device_id)
    changed = await service.update_agent(
        test_project.id, agent.id, AgentUpdate(bound_device_id=None),
        enrich_resources=False,
    )
    assert changed.bound_device_id is None
    assert changed.bound_device_policy_digest is None


@pytest.mark.asyncio
async def test_existing_device_policy_cannot_be_silently_rebound(
    monkeypatch, db_session, test_project
):
    validate = AsyncMock(return_value=SimpleNamespace(digest="a" * 64))
    monkeypatch.setattr(service_module.device_control_client, "validate_binding", validate)
    service = AgentService(db_session)
    device_id = str(uuid4())
    agent = await service.create_agent(
        test_project.id,
        AgentCreate(name="固定策略员工", model="fixture", bound_device_id=device_id),
    )
    validate.return_value = SimpleNamespace(digest="b" * 64)
    with pytest.raises(ConflictError, match="策略已变化"):
        await service.update_agent(
            test_project.id, agent.id,
            AgentUpdate(bound_device_id=device_id, name="普通改名"),
            enrich_resources=False,
        )
    assert agent.name == "固定策略员工"
    assert agent.bound_device_policy_digest == "a" * 64


@pytest.mark.asyncio
async def test_runtime_rejects_changed_device_policy_before_tool_discovery(monkeypatch):
    from app.runtime.tools.builder import agent_builder as module

    validate = AsyncMock(return_value=SimpleNamespace(digest="b" * 64))
    monkeypatch.setattr(module.device_control_client, "validate_binding", validate)
    builder = AgentBuilder(ToolsRuntimeSettings())
    agent = SimpleNamespace(
        id=uuid4(), project_id=str(uuid4()), bound_device_id=str(uuid4()),
        bound_device_policy_digest="a" * 64,
    )
    with pytest.raises(MCPToolError, match="策略"):
        await builder._build_device_mcp_tools(agent)
    validate.assert_awaited_once()


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
    policy_digest = "a" * 64
    monkeypatch.setattr(
        module.device_control_client, "validate_binding",
        AsyncMock(return_value=SimpleNamespace(digest=policy_digest)),
    )
    project, device = str(uuid4()), str(uuid4())
    agent = SimpleNamespace(
        id=uuid4(),
        project_id=project,
        bound_device_id=device,
        bound_device_policy_digest=policy_digest,
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
    assert claims["read_policy_digest"] == policy_digest
