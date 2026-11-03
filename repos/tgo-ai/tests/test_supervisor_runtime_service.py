from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.exceptions import NotFoundError
from app.models.internal import Agent as InternalAgent
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.schemas.agent_run import SupervisorRunRequest
from app.schemas.knowledge_evidence import KnowledgeEvidence


def _build_internal_agent(
    *, agent_id: uuid.UUID | None = None, name: str = "Support Agent"
) -> InternalAgent:
    now = datetime.now(timezone.utc)
    return InternalAgent(
        id=agent_id or uuid.uuid4(),
        name=name,
        instruction="You are helpful.",
        model="openai:gpt-4o",
        config={"temperature": 0.2},
        tools=[],
        collections=[],
        workflows=[],
        is_default=False,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_prepare_context_uses_explicit_agent_id(monkeypatch) -> None:
    agent = _build_internal_agent()
    project_id = uuid.uuid4()
    service = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock()
    )

    fake_agent_service = Mock()
    fake_agent_service.get_default_agent = AsyncMock()
    service._authorize_execution = AsyncMock(return_value=agent)
    service._reference_for_execution = AsyncMock(return_value=None)

    @asynccontextmanager
    async def fake_agent_service_context():
        yield fake_agent_service

    monkeypatch.setattr(
        service, "_agent_service_context", fake_agent_service_context, raising=False
    )
    monkeypatch.setattr(
        AIServiceClient,
        "get_agent",
        AsyncMock(return_value=agent),
        raising=False,
    )

    context, resolved_agent_id = await service._prepare_context(
        SupervisorRunRequest(
            message="hello",
            agent_id=str(agent.id),
            excluded_tool_ids=(uuid.uuid4(),),
            disable_tools=True,
            enable_memory=False,
            markdown=False,
            temperature=0.4,
        ),
        project_id,
        {"X-Request-ID": "req-explicit"},
    )

    assert context.agent.id == agent.id
    assert resolved_agent_id == str(agent.id)
    assert context.excluded_tool_ids
    assert context.disable_tools is True
    assert context.enable_memory is False
    assert context.markdown is False
    assert context.temperature == 0.4
    fake_agent_service.get_default_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_knowledge_run_uses_configured_rag_url(monkeypatch) -> None:
    agent = _build_internal_agent()
    project_id = uuid.uuid4()
    service = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock()
    )
    service._authorize_execution = AsyncMock(return_value=agent)
    service._reference_for_execution = AsyncMock(return_value=None)

    @asynccontextmanager
    async def fake_agent_service_context():
        yield Mock()

    monkeypatch.setattr(service, "_agent_service_context", fake_agent_service_context)
    monkeypatch.setattr(AIServiceClient, "get_agent", AsyncMock(return_value=agent))
    resolved = AsyncMock(return_value=agent)
    monkeypatch.setattr(
        "app.runtime.supervisor.application.service.resolve_agent_knowledge",
        resolved,
    )

    context, _ = await service._prepare_context(
        SupervisorRunRequest(
            agent_id=str(agent.id), message="退货条件是什么？",
            knowledge_channel="internal", require_current_knowledge=True,
        ),
        project_id,
        {"X-Request-ID": "req-workflow", "X-Execution-Channel": "internal"},
    )

    from app.config import settings

    assert context.rag_url == settings.rag_service_url
    assert context.require_current_knowledge is True
    resolved.assert_awaited_once()


@pytest.mark.asyncio
async def test_knowledge_only_run_isolates_customer_shadow_from_actions(monkeypatch) -> None:
    agent = _build_internal_agent()
    agent.bound_device_id = str(uuid.uuid4())
    agent.skills_enabled = True
    project_id = uuid.uuid4()
    service = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock()
    )
    service._authorize_execution = AsyncMock(return_value=agent)
    service._reference_for_execution = AsyncMock(return_value=None)

    @asynccontextmanager
    async def fake_agent_service_context():
        yield Mock()

    evidence = KnowledgeEvidence(
        status="matched", retrieved_at=datetime.now(timezone.utc),
        project_id=str(project_id), channel="wecom_kf",
    )
    monkeypatch.setattr(service, "_agent_service_context", fake_agent_service_context)
    monkeypatch.setattr(AIServiceClient, "get_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(
        "app.runtime.supervisor.application.service.resolve_agent_knowledge",
        AsyncMock(return_value=agent),
    )
    retrieve = AsyncMock(return_value=evidence)
    monkeypatch.setattr(
        "app.runtime.supervisor.application.service.retrieve_current_knowledge",
        retrieve,
    )

    context, _ = await service._prepare_context(
        SupervisorRunRequest(
            agent_id=str(agent.id), message="包的材质是什么？",
            knowledge_channel="wecom_kf", require_current_knowledge=True,
            knowledge_only=True, session_id="customer-session",
            user_id=str(uuid.uuid4()), enable_memory=True,
            ui_mode="text",
        ),
        project_id,
        {"X-Request-ID": "shadow-case", "X-Execution-Channel": "wecom_kf"},
    )

    retrieve.assert_awaited_once()
    assert context.knowledge_evidence is evidence
    assert context.disable_tools and context.enable_memory is False
    assert context.session_id is None and context.user_id is None
    assert context.agent.bound_device_id is None
    assert not context.agent.tools and not context.agent.workflows
    assert not context.agent.skills_enabled
    assert context.execution_reference is None


def test_knowledge_only_requires_governed_non_streaming_context() -> None:
    with pytest.raises(ValidationError):
        SupervisorRunRequest(message="test", knowledge_only=True)
    with pytest.raises(ValidationError):
        SupervisorRunRequest(
            message="test", knowledge_only=True,
            require_current_knowledge=True, knowledge_channel="wecom_kf",
            stream=True,
        )


@pytest.mark.asyncio
async def test_prepare_context_uses_project_default_agent_when_agent_id_missing(
    monkeypatch,
) -> None:
    agent = _build_internal_agent()
    project_id = uuid.uuid4()
    service = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock()
    )

    fake_agent_service = Mock()
    fake_agent_service.get_default_agent = AsyncMock()
    service._authorize_execution = AsyncMock(return_value=agent)
    service._reference_for_execution = AsyncMock(return_value=None)

    @asynccontextmanager
    async def fake_agent_service_context():
        yield fake_agent_service

    monkeypatch.setattr(
        service, "_agent_service_context", fake_agent_service_context, raising=False
    )
    monkeypatch.setattr(
        AIServiceClient,
        "get_default_agent",
        AsyncMock(return_value=agent),
        raising=False,
    )

    context, resolved_agent_id = await service._prepare_context(
        SupervisorRunRequest(message="hello"),
        project_id,
        {"X-Request-ID": "req-default"},
    )

    assert context.agent.id == agent.id
    assert resolved_agent_id == str(agent.id)
    AIServiceClient.get_default_agent.assert_awaited_once()
    fake_agent_service.get_default_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_returns_failure_when_project_default_agent_missing(
    monkeypatch,
) -> None:
    project_id = uuid.uuid4()
    service = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock()
    )
    service._agent_builder = Mock()
    service._agent_builder.build_agent = AsyncMock()
    service._agent_runner = Mock()
    service._agent_runner.run = AsyncMock()

    fake_agent_service = Mock()
    fake_agent_service.get_default_agent = AsyncMock(side_effect=NotFoundError("Agent"))

    @asynccontextmanager
    async def fake_agent_service_context():
        yield fake_agent_service

    monkeypatch.setattr(
        service, "_agent_service_context", fake_agent_service_context, raising=False
    )

    response = await service.run(
        SupervisorRunRequest(message="hello"),
        project_id,
        extra_headers={"X-Request-ID": "req-missing-default"},
    )

    assert response.success is False
    assert response.result is None
    assert response.metadata is None
    assert response.content == ""
    assert response.error == "Default agent not configured for project"
    service._agent_builder.build_agent.assert_not_awaited()
    service._agent_runner.run.assert_not_awaited()
