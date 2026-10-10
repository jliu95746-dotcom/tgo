"""Default knowledge inheritance remains tenant scoped and respects explicit bindings."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.models.internal import Agent, AgentCollection
from app.schemas.knowledge_availability import KnowledgeAvailability
from app.schemas.knowledge import KnowledgeChannel
from app.services.default_knowledge import resolve_agent_knowledge
from app.services.rag_service import rag_service_client


def employee(default=True):
    return Agent(
        id=uuid4(),
        project_id=str(uuid4()),
        name="test",
        model="test",
        is_default=default,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def availability(project, channel="wecom_kf"):
    return KnowledgeAvailability(
        project_id=project,
        channel=channel,
        collections=[
            {
                "id": uuid4(),
                "name": "可用资料",
                "eligible_chunk_count": 3,
                "total_chunk_count": 3,
                "blocked_reasons": [],
            },
            {
                "id": uuid4(),
                "name": "未开放资料",
                "eligible_chunk_count": 0,
                "total_chunk_count": 4,
                "blocked_reasons": ["channel_not_allowed"],
            },
        ],
    )


@pytest.mark.asyncio
async def test_default_employee_inherits_only_eligible_own_collections(monkeypatch):
    agent = employee()
    data = availability(agent.project_id)
    lookup = AsyncMock(return_value=data)
    monkeypatch.setattr(rag_service_client, "knowledge_availability", lookup)
    resolved = await resolve_agent_knowledge(agent, KnowledgeChannel.WECOM_KF)
    assert [c.collection_id for c in resolved.collections] == [
        str(data.collections[0].id)
    ]
    assert (
        agent.collections == []
    )  # Runtime inheritance never writes permanent bindings.
    lookup.assert_awaited_once_with(agent.project_id, KnowledgeChannel.WECOM_KF)


@pytest.mark.asyncio
@pytest.mark.parametrize("default", [True, False])
async def test_explicit_disabled_binding_never_inherits_other_knowledge(
    monkeypatch, default
):
    agent = employee(default)
    binding = AgentCollection(
        id=uuid4(), collection_id=str(uuid4()), enabled=False, display_name="explicit"
    )
    agent.collections = [binding]
    lookup = AsyncMock()
    monkeypatch.setattr(rag_service_client, "knowledge_availability", lookup)
    assert await resolve_agent_knowledge(agent, KnowledgeChannel.WECOM_KF) is agent
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_nondefault_unbound_employee_does_not_inherit(monkeypatch):
    agent = employee(False)
    lookup = AsyncMock()
    monkeypatch.setattr(rag_service_client, "knowledge_availability", lookup)
    assert await resolve_agent_knowledge(agent, KnowledgeChannel.WEB) is agent
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_tenant_or_channel_mismatch_is_rejected(monkeypatch):
    agent = employee()
    lookup = AsyncMock(return_value=availability(uuid4()))
    monkeypatch.setattr(rag_service_client, "knowledge_availability", lookup)
    with pytest.raises(ValueError, match="归属"):
        await resolve_agent_knowledge(agent, KnowledgeChannel.WECOM_KF)
    lookup.return_value = availability(agent.project_id, "web")
    with pytest.raises(ValueError, match="渠道"):
        await resolve_agent_knowledge(agent, KnowledgeChannel.WECOM_KF)


@pytest.mark.asyncio
async def test_lookup_failure_is_not_disguised_as_no_knowledge(monkeypatch):
    agent = employee()
    monkeypatch.setattr(
        rag_service_client,
        "knowledge_availability",
        AsyncMock(side_effect=TimeoutError()),
    )
    with pytest.raises(TimeoutError):
        await resolve_agent_knowledge(agent, KnowledgeChannel.WEB)


@pytest.mark.asyncio
async def test_newly_approved_knowledge_is_discovered_without_persisting_bindings(
    monkeypatch,
):
    agent = employee()
    blocked = availability(agent.project_id)
    blocked.collections[0].eligible_chunk_count = 0
    ready = availability(agent.project_id)
    lookup = AsyncMock(side_effect=[blocked, ready])
    monkeypatch.setattr(rag_service_client, "knowledge_availability", lookup)
    assert (
        await resolve_agent_knowledge(agent, KnowledgeChannel.WECOM_KF)
    ).collections == []
    assert (
        len(
            (
                await resolve_agent_knowledge(agent, KnowledgeChannel.WECOM_KF)
            ).collections
        )
        == 1
    )
    assert agent.collections == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("skip", [None, "expression", "disable_tools"])
async def test_stream_and_nonstream_prepare_same_knowledge_but_expression_skips_it(
    monkeypatch, stream, skip
):
    from app.runtime.supervisor.application import service as runtime_module
    from app.runtime.supervisor.application.service import SupervisorRuntimeService
    from app.runtime.supervisor.infrastructure.services import AIServiceClient
    from app.schemas.agent_run import SupervisorRunRequest
    from app.streaming.event_emitter import StreamingEventEmitter
    from tests.test_supervisor_cancellation import domain_events

    agent = employee()
    data = availability(agent.project_id)
    lookup = AsyncMock(return_value=data)
    monkeypatch.setattr(rag_service_client, "knowledge_availability", lookup)
    monkeypatch.setattr(
        AIServiceClient, "get_default_agent", AsyncMock(return_value=agent)
    )

    @asynccontextmanager
    async def service_context():
        yield Mock()

    runtime = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock(_settings=None)
    )
    monkeypatch.setattr(runtime, "_agent_service_context", service_context)
    seen = []

    async def build(context):
        seen.append(context)
        raise ValueError("stop before provider")

    runtime._agent_builder.build_agent = AsyncMock(side_effect=build)
    request = SupervisorRunRequest(
        message="介绍产品",
        rag_url="http://rag.test",
        knowledge_channel="wecom_kf",
        response_purpose="expression" if skip == "expression" else "standard",
        disable_tools=skip == "disable_tools",
    )
    if stream:
        emitter = StreamingEventEmitter(str(uuid4()), str(uuid4()))
        monkeypatch.setattr(runtime_module, "get_event_emitter", lambda *args: emitter)
        response = await runtime.stream(
            request,
            UUID(agent.project_id),
            http_request=SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
        )
        events = await domain_events(response)
        assert any(e["event_type"] == "workflow_failed" for e in events)
    else:
        response = await runtime.run(request, UUID(agent.project_id))
        assert response.success is False
    assert len(seen) == 1
    if skip:
        lookup.assert_not_awaited()
        assert seen[0].agent.collections == []
    else:
        assert [c.collection_id for c in seen[0].agent.collections] == [
            str(data.collections[0].id)
        ]
        lookup.assert_awaited_once()
