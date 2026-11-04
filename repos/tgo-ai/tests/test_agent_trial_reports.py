"""Durable, scoped and idempotent reports never invent a capability pass."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.agent import Agent as StoredAgent
from app.models.agent_test_report import AgentTestReport
from app.schemas.agent_run import SupervisorRunResponse
from app.schemas.agent_trials import AgentTrialRequest
from app.services.agent_trials import AgentTrialService
from tests.test_agent_knowledge_trial import employee, evidence


async def prepared(db, project, monkeypatch, status="matched"):
    from app.runtime.supervisor.infrastructure.services import AIServiceClient

    agent = StoredAgent(
        id=uuid4(), project_id=project.id, name="停用客服", model="test",
        is_active=False, collections=[], workflows=[], tools=[],
    )
    db.add(agent)
    await db.commit()
    internal = employee().model_copy(update={"id": agent.id, "project_id": str(project.id)})
    monkeypatch.setattr(AIServiceClient, "get_trial_agent", AsyncMock(return_value=internal))
    from app.schemas.agent_trials import TrialKnowledgeSnapshot
    from app.services import agent_trial_dependencies, agent_trials
    snapshot = TrialKnowledgeSnapshot(channel="web", binding_mode="unbound", collections=[],
        issues=["no_binding"], captured_at=internal.created_at)
    lookup = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(agent_trials, "read_knowledge_snapshot", lookup)
    monkeypatch.setattr(agent_trial_dependencies, "read_knowledge_snapshot", lookup)
    runtime = Mock(run_knowledge_trial=AsyncMock(return_value=SupervisorRunResponse(
        success=True, message="completed", content="测试联系 test@example.com",
        knowledge_evidence=evidence(internal, status),
    )))
    return agent, runtime, AgentTrialService(db, runtime)


@pytest.mark.asyncio
async def test_report_is_durable_redacted_and_idempotent(db_session, test_project, monkeypatch):
    agent, runtime, service = await prepared(db_session, test_project, monkeypatch)
    creator = uuid4()
    request = AgentTrialRequest(message="邮箱 test@example.com 的材质咨询", request_key=uuid4())
    report = await service.start(test_project.id, creator, agent.id, request)
    again = await service.start(test_project.id, creator, agent.id, request)
    assert report.id == again.id
    assert report.status == "completed" and report.assessment == "not_reviewed"
    assert report.can_enable is False
    assert "test@example.com" not in report.model_dump_json()
    assert report.sources and report.sources[0].content_digest
    stored = await db_session.scalar(select(AgentTestReport).where(AgentTestReport.id == report.id))
    assert stored and "test@example.com" not in stored.question
    def history(value):
        return value.model_dump(exclude={"applicability"})
    assert history(await service.get(test_project.id, creator, agent.id, report.id)) == history(again)
    assert history(await service.match_request(test_project.id, creator, agent.id, request)) == history(again)
    runtime.run_knowledge_trial.assert_awaited_once()
    await db_session.refresh(agent)
    assert agent.is_active is False


@pytest.mark.asyncio
async def test_same_key_cannot_replace_a_question(db_session, test_project, monkeypatch):
    agent, runtime, service = await prepared(db_session, test_project, monkeypatch)
    creator, key = uuid4(), uuid4()
    await service.start(test_project.id, creator, agent.id, AgentTrialRequest(message="材质？", request_key=key))
    with pytest.raises(HTTPException) as error:
        await service.start(test_project.id, creator, agent.id, AgentTrialRequest(message="价格？", request_key=key))
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as recovered:
        await service.match_request(test_project.id, creator, agent.id, AgentTrialRequest(message="价格？", request_key=key))
    assert recovered.value.status_code == 409
    runtime.run_knowledge_trial.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["no_match", "unavailable", "over_budget", "skipped"])
async def test_no_evidence_is_not_a_capability_pass(db_session, test_project, monkeypatch, status):
    agent, _, service = await prepared(db_session, test_project, monkeypatch, status)
    report = await service.start(test_project.id, uuid4(), agent.id, AgentTrialRequest(message="材质？", request_key=uuid4()))
    assert report.status == "blocked" and report.can_enable is False
    assert report.knowledge_status == status
    assert report.assessment == "not_reviewed"


@pytest.mark.asyncio
async def test_reports_and_agent_lookup_are_owner_scoped(db_session, test_project, monkeypatch):
    agent, runtime, service = await prepared(db_session, test_project, monkeypatch)
    creator = uuid4()
    report = await service.start(test_project.id, creator, agent.id, AgentTrialRequest(message="材质？", request_key=uuid4()))
    for project, author in [(uuid4(), creator), (test_project.id, uuid4())]:
        with pytest.raises(HTTPException) as error:
            await service.get(project, author, agent.id, report.id)
        assert error.value.status_code == 404
    with pytest.raises(Exception):
        await service.start(uuid4(), creator, agent.id, AgentTrialRequest(message="材质？", request_key=uuid4()))
    runtime.run_knowledge_trial.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_error_cannot_leak_provider_details(db_session, test_project, monkeypatch):
    agent, runtime, service = await prepared(db_session, test_project, monkeypatch)
    runtime.run_knowledge_trial.side_effect = RuntimeError("Authorization: secret-provider-value")
    report = await service.start(test_project.id, uuid4(), agent.id, AgentTrialRequest(message="材质？", request_key=uuid4()))
    assert report.status == "failed"
    assert "secret-provider-value" not in report.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code,hint", [
    ("unavailable", "knowledge_unavailable", "向量模型"),
    ("over_budget", "knowledge_over_budget", "资料"),
])
async def test_retrieval_failure_is_durable_specific_and_never_generates_an_answer(
    db_session, test_project, monkeypatch, status, code, hint,
):
    agent, runtime, service = await prepared(db_session, test_project, monkeypatch, status)
    runtime.run_knowledge_trial.return_value = SupervisorRunResponse(
        success=False, message="knowledge retrieval blocked", content="",
        knowledge_evidence=evidence(employee().model_copy(update={"project_id": str(test_project.id)}), status),
        error="private-provider-detail",
    )
    creator = uuid4()
    request = AgentTrialRequest(message="材质？", request_key=uuid4())
    report = await service.start(test_project.id, creator, agent.id, request)
    recovered = await service.start(test_project.id, creator, agent.id, request)

    assert report.status == "blocked" and report.can_enable is False
    assert report.knowledge_status == status and report.error_code == code
    assert hint in report.error_message
    assert report.answer == "" and report.sources == []
    assert recovered.id == report.id and recovered.error_code == code
    assert "private-provider-detail" not in report.model_dump_json()
    runtime.run_knowledge_trial.assert_awaited_once()
