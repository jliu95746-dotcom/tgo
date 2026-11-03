"""Inactive trials query governed facts without activating or running actions."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.internal import Agent, AgentCollection
from app.models.agent import Agent as StoredAgent
from app.runtime.core.exceptions import DataMappingError
from app.runtime.supervisor.infrastructure.services import _convert_agent
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.schemas.agent_run import SupervisorRunRequest, SupervisorRunResponse
from app.schemas.agent_trials import AgentTrialRequest
from app.schemas.knowledge_evidence import KnowledgeDocument, KnowledgeEvidence
from app.services.agent_trial_fingerprint import configuration_fingerprint


def employee():
    return Agent(
        id=uuid4(), project_id=str(uuid4()), name="澜月客服", model="test",
        instruction="依据当前资料回答", created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC), bound_device_id=str(uuid4()),
        skills_enabled=True, config={"enable_memory": True},
    )


def evidence(agent, status="matched"):
    return KnowledgeEvidence(
        status=status, retrieved_at=datetime.now(UTC),
        project_id=agent.project_id, channel="web",
        documents=[KnowledgeDocument(
            collection_id=str(uuid4()), document_id=str(uuid4()),
            content="商品为牛皮材质", relevance_score=0.9,
        )] if status == "matched" else [],
    )


def test_private_trial_conversion_does_not_change_normal_inactive_guard():
    stored = StoredAgent(
        id=uuid4(), project_id=uuid4(), name="停用客服", model="test",
        is_active=False, is_default=False, instruction="依据资料回答",
        collections=[], tools=[], workflows=[], skills_enabled=False,
        is_remote_store_agent=False,
    )
    with pytest.raises(DataMappingError):
        _convert_agent(stored)
    internal = _convert_agent(stored, allow_inactive_trial=True)
    assert internal.id == stored.id and stored.is_active is False
    with pytest.raises(DataMappingError):
        _convert_agent(stored)


@pytest.mark.parametrize("field", [
    "project_id", "created_by", "success", "mcp_url", "rag_url",
    "allow_inactive", "provider_credentials", "session_id", "mode",
])
def test_trial_request_does_not_accept_execution_or_identity_overrides(field):
    with pytest.raises(ValidationError):
        AgentTrialRequest(message="什么材质？", request_key=uuid4(), **{field: "forged"})


def test_trial_request_requires_a_question_and_keeps_normal_run_restriction():
    with pytest.raises(ValidationError):
        AgentTrialRequest(message="  ", request_key=uuid4())
    with pytest.raises(ValidationError):
        SupervisorRunRequest(message="hello", allow_inactive=True)


@pytest.mark.asyncio
async def test_trial_queries_before_disabling_tools_and_isolates_memory(monkeypatch):
    from app.runtime.supervisor.application import service

    agent = employee()
    collection = AgentCollection(
        id=uuid4(), collection_id=str(uuid4()), display_name="商品资料",
    )
    resolved = agent.model_copy(update={"collections": [collection]})
    lookup = AsyncMock(return_value=resolved)
    monkeypatch.setattr(service, "resolve_agent_knowledge", lookup)

    async def retrieve(context):
        assert context.disable_tools is False
        assert context.knowledge_channel.value == "web"
        assert context.agent.collections == [collection]
        return evidence(agent)

    monkeypatch.setattr(service, "retrieve_current_knowledge", retrieve)
    runtime = SupervisorRuntimeService.__new__(SupervisorRuntimeService)
    runtime._agent_builder = Mock(build_agent=AsyncMock(return_value=object()))
    runtime._agent_runner = Mock(run=AsyncMock(return_value=SupervisorRunResponse(
        success=True, message="completed", content="牛皮材质",
    )))
    request = AgentTrialRequest(message="什么材质？", request_key=uuid4())
    response = await runtime.run_knowledge_trial(agent, request)
    context = runtime._agent_builder.build_agent.await_args.args[0]
    assert context.session_id is None and context.user_id is None
    assert context.enable_memory is False and context.disable_tools is True
    assert context.agent.tools == [] and context.agent.workflows == []
    assert context.agent.bound_device_id is None
    assert context.agent.skills_enabled is False
    assert context.require_current_knowledge and context.knowledge_evidence
    assert context.ui_mode == "text"
    assert response.knowledge_evidence.status == "matched"
    assert agent.bound_device_id and agent.skills_enabled
    assert agent.collections == []


@pytest.mark.asyncio
async def test_remote_employee_cannot_be_silently_replaced_by_local_trial():
    runtime = SupervisorRuntimeService.__new__(SupervisorRuntimeService)
    agent = employee().model_copy(update={"is_remote_store_agent": True})
    with pytest.raises(ValueError, match="远程"):
        await runtime.run_knowledge_trial(
            agent, AgentTrialRequest(message="hello", request_key=uuid4())
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unavailable", "over_budget"])
async def test_unverifiable_retrieval_does_not_start_answer_generation(monkeypatch, status):
    from app.runtime.supervisor.application import service

    agent = employee()
    monkeypatch.setattr(service, "resolve_agent_knowledge", AsyncMock(return_value=agent))
    monkeypatch.setattr(service, "retrieve_current_knowledge", AsyncMock(return_value=evidence(agent, status)))
    runtime = SupervisorRuntimeService.__new__(SupervisorRuntimeService)
    runtime._agent_builder = Mock(build_agent=AsyncMock(return_value=object()))
    runtime._agent_runner = Mock(run=AsyncMock(return_value=SupervisorRunResponse(
        success=True, message="completed", content="不应生成的回答",
    )))

    response = await runtime.run_knowledge_trial(
        agent, AgentTrialRequest(message="这款包是什么材质？", request_key=uuid4())
    )

    assert response.success is False
    assert response.content == ""
    assert response.knowledge_evidence.status == status
    runtime._agent_builder.build_agent.assert_not_awaited()
    runtime._agent_runner.run.assert_not_awaited()


def test_fingerprint_ignores_display_name_but_tracks_business_config():
    agent = employee()
    initial = configuration_fingerprint(agent)
    assert initial == configuration_fingerprint(agent.model_copy(update={"name": "新昵称"}))
    assert initial != configuration_fingerprint(agent.model_copy(update={"instruction": "新职责"}))
    assert initial != configuration_fingerprint(agent.model_copy(update={"model": "other"}))
    assert len(initial) == 64
