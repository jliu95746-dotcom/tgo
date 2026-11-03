"""Complete current evidence is required across every configured channel."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Agent, AgentActivationPolicy, AgentTestReport, BaseModel
from app.schemas.agent_activation import AgentActivationRequirements
from app.schemas.agent_knowledge_capabilities import (
    AgentKnowledgeCapabilities, KnowledgeCapabilityCoverage, KnowledgeCaseCoverage,
)
from app.services.agent_activation_readiness import AgentActivationReadinessService
from app.services.agent_trial_fingerprint import configuration_fingerprint
from app.exceptions import AuthorizationError, ValidationError
from app.models.workflow import AgentWorkflow
from app.schemas.device_access import BoundDeviceReadPolicy
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from tests.test_agent_activation_requirements_client import fixture as requirements_fixture
from tests.test_agent_knowledge_trial import employee


@pytest_asyncio.fixture
async def setup(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'readiness.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        project, actor, identifier = uuid4(), uuid4(), uuid4()
        stored = Agent(id=identifier, project_id=project, name="知识客服", model="fixture",
            is_active=False, skills_enabled=False, tools=[], workflows=[], collections=[])
        db.add(stored)
        await db.commit()
        internal = employee().model_copy(update={"id": identifier, "project_id": str(project),
            "bound_device_id": None, "skills_enabled": False, "config": {}, "model_owner_revision": "a" * 64})
        query, body = requirements_fixture()
        body.update(project_id=str(project), requested_by=str(actor))
        body["requirements_revision"] = AgentActivationRequirements.model_construct(
            **body).definition_revision()
        requirements = AgentActivationRequirements.model_validate(body)
        policy = AgentActivationPolicy(project_id=project, agent_id=identifier, channel="web",
            template_id="knowledge", template_version=1, revision=1,
            requirements_revision=requirements.requirements_revision, created_by=actor, updated_by=actor)
        db.add(policy)
        now, expiry = datetime.now(UTC), datetime.now(UTC) + timedelta(hours=1)
        cases = []
        for spec in requirements.knowledge_cases:
            report_id, review_id = uuid4(), uuid4()
            db.add(AgentTestReport(id=report_id, project_id=project, agent_id=identifier,
                created_by=actor, request_key=uuid4(), request_digest="b" * 64,
                status="completed", channel="web", question=spec.question, answer="测试答复",
                sources=[], expires_at=expiry, configuration_fingerprint=configuration_fingerprint(internal)))
            cases.append(KnowledgeCaseCoverage(case=spec, status="passed", report_id=report_id,
                review_id=review_id, review_revision=1, valid_until=expiry))
        await db.commit()
        coverage = AgentKnowledgeCapabilities(agent_id=identifier, reviewed_by=actor, channel="web",
            enabled=False, knowledge_status="available", checked_at=now, capabilities=[KnowledgeCapabilityCoverage(
                template_id="knowledge", template_version=1, status="passed", cases=cases,
                answer_cases_passed=2, answer_cases_required=2, safety_cases_passed=1, safety_cases_required=1,
                valid_until=expiry)])
        owner, configuration, knowledge = AsyncMock(), AsyncMock(return_value=internal), AsyncMock()
        owner.get_agent_activation_requirements.return_value = requirements
        knowledge.read.return_value = coverage
        service = AgentActivationReadinessService(db, owner=owner, configuration_reader=configuration,
            knowledge=knowledge)
        yield db, stored, actor, policy, internal, owner, coverage, configuration, service
    await engine.dispose()


async def bind_tested_device(setup, monkeypatch):
    from app.services import agent_service as service_module

    db, agent, _, _, internal, _, _, _, _ = setup
    device_id, digest = uuid4(), "a" * 64
    agent.bound_device_id = str(device_id)
    agent.bound_device_policy_digest = digest
    internal.bound_device_id = str(device_id)
    internal.bound_device_policy_digest = digest
    await db.execute(update(AgentTestReport).where(
        AgentTestReport.agent_id == agent.id,
    ).values(configuration_fingerprint=configuration_fingerprint(internal)))
    await db.commit()
    policy = BoundDeviceReadPolicy(
        project_id=agent.project_id, device_id=device_id, tool_name="fs_read",
        status="active", verified=True, digest=digest,
    )
    check = AsyncMock(return_value=policy)
    monkeypatch.setattr(service_module.device_control_client, "validate_binding", check)
    return device_id, policy, check


@pytest.mark.asyncio
async def test_current_device_read_test_can_authorize_bound_employee(setup, monkeypatch):
    _, agent, actor, _, _, _, _, _, service = setup
    device_id, _, check = await bind_tested_device(setup, monkeypatch)

    result = await service.read(agent.project_id, actor, agent.id)

    assert result.can_enable and not result.blockers
    check.assert_awaited_with(agent.project_id, str(device_id))


@pytest.mark.asyncio
async def test_device_policy_change_before_activation_check_rejects_enablement(setup, monkeypatch):
    _, agent, actor, _, _, _, _, _, service = setup
    _, policy, check = await bind_tested_device(setup, monkeypatch)
    check.return_value = policy.model_copy(update={"digest": "b" * 64})

    result = await service.read(agent.project_id, actor, agent.id)

    assert not result.can_enable
    assert any(row.resource == "device" for row in result.blockers)
    assert check.await_count == 1


@pytest.mark.asyncio
async def test_disconnected_device_requires_a_new_read_test(setup, monkeypatch):
    _, agent, actor, _, _, _, _, _, service = setup
    await bind_tested_device(setup, monkeypatch)
    from app.services import agent_service as service_module

    check = service_module.device_control_client.validate_binding
    check.side_effect = ValidationError("设备测试失效", "bound_device_id")

    result = await service.read(agent.project_id, actor, agent.id)

    assert not result.can_enable
    assert any(row.resource == "device" and row.code == "dependency_not_verified"
               for row in result.blockers)


@pytest.mark.asyncio
async def test_all_current_cases_can_authorize_only_this_employee_configuration(setup):
    _, agent, actor, _, _, owner, _, _, service = setup
    result = await service.read(agent.project_id, actor, agent.id)
    assert result.can_enable and result.scope == "employee_activation"
    assert not result.enabled and not result.blockers and result.channels[0].status == "passed"
    assert result.configuration_fingerprint and result.model_owner_revision
    assert owner.get_agent_activation_requirements.await_count == 2
    assert agent.is_active is False


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["model_epoch", "configuration", "missing_model_proof", "catalog", "expired",
    "skills", "device", "remote", "policy_missing", "requirements", "other_channel"])
async def test_incomplete_or_changed_dependencies_never_authorize_enablement(setup, fault):
    db, agent, actor, policy, internal, owner, coverage, configuration, service = setup
    if fault == "model_epoch":
        internal.model_owner_revision = "c" * 64
    elif fault == "configuration":
        internal.instruction = "更新后的岗位职责"
    elif fault == "missing_model_proof":
        internal.model_owner_revision = None
    elif fault == "catalog":
        coverage.capabilities[0].cases.pop()
    elif fault == "expired":
        coverage.capabilities[0].valid_until = datetime.now(UTC) - timedelta(seconds=1)
    elif fault == "skills":
        agent.skills_enabled = True
    elif fault == "device":
        agent.bound_device_id = str(uuid4())
    elif fault == "remote":
        agent.is_remote_store_agent = True
    elif fault == "policy_missing":
        policy.deleted_at = datetime.now(UTC)
    elif fault == "requirements":
        policy.requirements_revision = "f" * 64
    else:
        policy.channel = "wecom_kf"
    await db.commit()
    result = await service.read(agent.project_id, actor, agent.id)
    assert result.can_enable is False and result.blockers
    assert agent.is_active is False


@pytest.mark.asyncio
async def test_late_change_cannot_leave_an_earlier_success_visible(setup):
    _, agent, actor, _, internal, _, _, configuration, service = setup
    configuration.side_effect = [internal, internal.model_copy(update={"model_owner_revision": "d" * 64})]
    result = await service.read(agent.project_id, actor, agent.id)
    assert not result.can_enable and result.blockers


@pytest.mark.asyncio
async def test_late_owner_failure_and_revoked_role_cannot_reuse_success(setup):
    _, agent, actor, _, _, owner, _, _, service = setup
    original = owner.get_agent_activation_requirements.return_value
    owner.get_agent_activation_requirements.side_effect = [original, RuntimeError("synthetic-private-marker")]
    result = await service.read(agent.project_id, actor, agent.id)
    assert not result.can_enable and result.blockers
    assert "synthetic-private-marker" not in result.model_dump_json()
    owner.get_agent_activation_requirements.side_effect = AuthorizationError("权限已撤销")
    with pytest.raises(AuthorizationError):
        await service.read(agent.project_id, actor, agent.id)


@pytest.mark.asyncio
async def test_enabled_workflow_needs_independent_proof_but_disabled_binding_does_not(setup):
    db, agent, actor, _, _, _, _, _, service = setup
    binding = AgentWorkflow(agent_id=agent.id, workflow_id=str(uuid4()), enabled=False)
    db.add(binding)
    await db.commit()
    assert (await service.read(agent.project_id, actor, agent.id)).can_enable
    binding.enabled = True
    await db.commit()
    assert not (await service.read(agent.project_id, actor, agent.id)).can_enable
    binding.soft_delete()
    await db.commit()
    assert (await service.read(agent.project_id, actor, agent.id)).can_enable


@pytest.mark.asyncio
async def test_default_configuration_reader_refreshes_existing_identity_map(setup, monkeypatch):
    db, agent, actor, _, internal, _, _, _, service = setup

    async def load(reader, identifier, **kwargs):
        stored = await reader._agent_service.db.get(Agent, identifier)
        return internal.model_copy(update={"instruction": stored.instruction})

    monkeypatch.setattr(AIServiceClient, "get_trial_agent", load)
    old = await service._configuration(agent.project_id, actor, agent.id)
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as other:
        await other.execute(update(Agent).where(Agent.id == agent.id).values(instruction="其他请求修改的职责"))
        await other.commit()
    current = await service._configuration(agent.project_id, actor, agent.id)
    assert current.instruction == "其他请求修改的职责" and current.instruction != old.instruction
