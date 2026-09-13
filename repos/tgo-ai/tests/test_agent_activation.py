"""Activation must persist and gate new runs without deleting agent resources."""
from unittest.mock import AsyncMock, Mock
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError as SchemaValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.agent import AgentCreate, AgentUpdate
from app.services.agent_service import AgentService
from app.runtime.supervisor.infrastructure.services import (
    _convert_agent,
    AIServiceClient,
    DataMappingError,
)
from tests.test_bound_http_tool_runtime import bound_agent
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.runtime.supervisor.application import service as runtime_module
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.schemas.agent_run import SupervisorRunRequest
from app.streaming.event_emitter import StreamingEventEmitter
from tests.test_supervisor_cancellation import domain_events


def test_disabled_employee_cannot_become_an_executable_agent() -> None:
    agent, _ = bound_agent()
    agent.is_active = False
    with pytest.raises(DataMappingError, match="已停用"):
        _convert_agent(agent)


def test_reenabled_employee_keeps_resources() -> None:
    agent, tool = bound_agent()
    agent.is_active = True
    result = _convert_agent(agent, {tool.id: {"enabled": True}})
    assert result.tools[0].tool_id == tool.id


@pytest.mark.asyncio
async def test_creation_persists_requested_activation() -> None:
    database = Mock(flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
    service = AgentService(cast(AsyncSession, database))
    created = await service.create_agent(
        uuid4(), AgentCreate(name="验收", model="fixture", is_active=False)
    )
    assert created.is_active is False
    database.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_status_only_update_keeps_all_existing_resources() -> None:
    agent, tool = bound_agent()
    database = Mock(commit=AsyncMock(), refresh=AsyncMock())
    service = AgentService(cast(AsyncSession, database))
    service.get_agent = AsyncMock(return_value=agent)
    for enabled in (False, True):
        updated = await service.update_agent(
            agent.project_id, agent.id, AgentUpdate(is_active=enabled)
        )
        assert updated.is_active is enabled
        assert updated.tools == [tool]
    assert database.commit.await_count == 2


def test_activation_cannot_be_null() -> None:
    with pytest.raises(SchemaValidationError):
        AgentUpdate(is_active=None)
    assert AgentUpdate().model_dump(exclude_unset=True) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("default", [False, True])
async def test_explicit_and_default_runtime_loads_reject_disabled_agents(
    default: bool,
) -> None:
    agent, _ = bound_agent()
    agent.is_active = False
    database = Mock(
        execute=AsyncMock(return_value=Mock(scalars=lambda: Mock(all=lambda: [])))
    )
    service = Mock(
        db=database,
        get_agent=AsyncMock(return_value=agent),
        get_default_agent=AsyncMock(return_value=agent),
    )
    client = AIServiceClient(service, agent.project_id)
    with pytest.raises(DataMappingError, match="已停用"):
        if default:
            await client.get_default_agent({})
        else:
            await client.get_agent(str(agent.id), {})


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("default", [False, True])
async def test_disabled_employee_stops_before_model_build(
    monkeypatch, stream: bool, default: bool
) -> None:
    agent, _ = bound_agent()
    agent.is_active = False
    database = Mock(
        execute=AsyncMock(return_value=Mock(scalars=lambda: Mock(all=lambda: [])))
    )
    service = Mock(
        db=database,
        get_agent=AsyncMock(return_value=agent),
        get_default_agent=AsyncMock(return_value=agent),
    )

    @asynccontextmanager
    async def service_context():
        yield service

    runtime = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock(_settings=None)
    )
    monkeypatch.setattr(runtime, "_agent_service_context", service_context)
    runtime._agent_builder.build_agent = AsyncMock()
    request = SupervisorRunRequest(
        message="activation check", agent_id=None if default else str(agent.id)
    )
    if stream:
        emitter = StreamingEventEmitter(str(uuid4()), str(uuid4()))
        monkeypatch.setattr(runtime_module, "get_event_emitter", lambda *args: emitter)
        response = await runtime.stream(
            request,
            agent.project_id,
            http_request=SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
        )
        events = await domain_events(response)
        assert len(events) == 1
        assert "已停用" in str(events[0])
        assert events[0]["event_type"] == "workflow_failed"
    else:
        response = await runtime.run(request, agent.project_id)
        assert response.success is False
        assert "已停用" in response.error
    runtime._agent_builder.build_agent.assert_not_awaited()
