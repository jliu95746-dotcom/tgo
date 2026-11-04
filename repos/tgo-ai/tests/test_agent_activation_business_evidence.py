"""Business assessment coverage and exact evidence are distinct from runtime availability."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.agent_business_connection import AgentBusinessConnection
from app.models.workflow import AgentWorkflow
from app.schemas.agent_activation import AgentActivationRequirements
from app.schemas.agent_activation_requests import ActivationExpectation
from app.schemas.agent_business_reviews import AgentBusinessCapabilities, BusinessOperationCapability
from app.services.agent_activation_guard import ActivationConfirmationConflict, require_activation
from app.services.agent_activation_readiness import AgentActivationReadinessService
from app.services.workflow_service import WorkflowIntegrationRelease
from app.services.agent_activation_requests import command_digest
from app.schemas.agent_activation_requests import AgentActivationRequest
import hashlib
import json
from tests.test_agent_activation_readiness import setup as setup

async def business_setup(setup):
    db, agent, actor, policy, _, owner, _, _, service = setup
    body = owner.get_agent_activation_requirements.return_value.model_dump(mode="json")
    body.update(template_id="logistics", required_resources=["model", "connection"],
                required_capabilities=["scoped_order_query", "scoped_logistics_query"], knowledge_cases=[])
    body["requirements_revision"] = AgentActivationRequirements.model_construct(**body).definition_revision()
    owner.get_agent_activation_requirements.return_value = AgentActivationRequirements.model_validate(body)
    policy.template_id, policy.requirements_revision = "logistics", body["requirements_revision"]
    binding = AgentBusinessConnection(project_id=agent.project_id, agent_id=agent.id,
        connection_id=uuid4(), operations=["order_query", "logistics_query"], enabled=True,
        revision=1, configuration_revision=1, authorization_revision=1,
        resource_revision="d" * 64, configured_by=actor)
    db.add(binding)
    await db.commit()
    coverage = AgentBusinessCapabilities(project_id=agent.project_id, agent_id=agent.id,
        checked_by=actor, checked_at=datetime.now(UTC), capabilities=[BusinessOperationCapability(
            binding_id=binding.id, binding_revision=1, connection_id=binding.connection_id,
            operation=operation, status="passed", report_id=uuid4(), review_id=uuid4(),
            valid_until=datetime.now(UTC) + timedelta(hours=1), issues=[])
        for operation in ("order_query", "logistics_query")])
    service.business = AsyncMock(read=AsyncMock(return_value=coverage))
    from app.schemas.agent_business_execution import BusinessRuntimeReadiness
    service.runtime = AsyncMock(return_value=BusinessRuntimeReadiness(project_id=agent.project_id,
        requested_by=actor, available=False, operations=[], checked_at=datetime.now(UTC)))
    return db, agent, actor, binding, coverage, service


@pytest.mark.asyncio
async def test_current_business_reviews_can_enable_only_with_formal_runtime(setup):
    _, agent, actor, _, _, service = await business_setup(setup)
    from app.schemas.agent_business_execution import BusinessRuntimeReadiness
    service.runtime.return_value = BusinessRuntimeReadiness(project_id=agent.project_id,
        requested_by=actor, available=True,
        operations=["order_query", "logistics_query", "customer_profile_query", "membership_query"],
        checked_at=datetime.now(UTC))
    result = await service.read(agent.project_id, actor, agent.id)
    assert result.can_enable and not result.blockers
    assert agent.is_active is False


@pytest.mark.asyncio
@pytest.mark.parametrize("reviewed", [False, True])
async def test_extra_customer_operation_requires_its_own_current_review(setup, reviewed):
    _, agent, actor, binding, coverage, service = await business_setup(setup)
    from app.schemas.agent_business_execution import BusinessRuntimeReadiness

    binding.operations.append("membership_query")
    await service.db.commit()
    coverage.capabilities.append(BusinessOperationCapability(
        binding_id=binding.id, binding_revision=binding.revision,
        connection_id=binding.connection_id, operation="membership_query",
        status="passed" if reviewed else "review_required",
        report_id=uuid4() if reviewed else None,
        review_id=uuid4() if reviewed else None,
        valid_until=datetime.now(UTC) + timedelta(hours=1) if reviewed else None,
        issues=[],
    ))
    service.runtime.return_value = BusinessRuntimeReadiness(
        project_id=agent.project_id, requested_by=actor, available=True,
        operations=["order_query", "logistics_query", "customer_profile_query", "membership_query"],
        checked_at=datetime.now(UTC),
    )
    result = await service.read(agent.project_id, actor, agent.id)
    assert result.can_enable is reviewed
    assert result.channels[0].status == ("passed" if reviewed else "needs_retest")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "wrong_digest", "wrong_release_id", "wrong_connection",
    "wrong_operation", "unsafe_node", "missing_summary", "unpublished", "unpinned",
    "late_change"])
async def test_governed_workflow_requires_exact_published_business_dependencies(setup, monkeypatch, fault):
    db, agent, actor, business_binding, coverage, service = await business_setup(setup)
    from app.schemas.agent_business_execution import BusinessRuntimeReadiness
    from app.services import agent_activation_readiness as readiness_module

    service.runtime.return_value = BusinessRuntimeReadiness(project_id=agent.project_id,
        requested_by=actor, available=True,
        operations=["order_query", "logistics_query", "customer_profile_query", "membership_query"],
        checked_at=datetime.now(UTC))
    if fault == "wrong_operation":
        coverage.capabilities[1].connection_id = uuid4()
    workflow_id, release_id = str(uuid4()), str(uuid4())
    binding = AgentWorkflow(agent_id=agent.id, workflow_id=workflow_id, enabled=True,
        release_version=1, release_id=release_id, release_digest="a" * 64)
    if fault == "unpinned":
        binding.release_digest = None
    db.add(binding)
    await db.commit()
    release = WorkflowIntegrationRelease.model_validate({
        "workflow_id": workflow_id, "release_version": 1,
        "release_id": str(uuid4()) if fault == "wrong_release_id" else release_id,
        "definition_digest": "b" * 64 if fault == "wrong_digest" else "a" * 64,
        "input_parameters": [{"name": "order_no", "type": "string", "required": True}],
        **({} if fault == "missing_summary" else {
            "node_types": ["input", "business_query", "answer", "tool"] if fault == "unsafe_node"
                else ["input", "business_query", "answer"],
            "required_business_queries": [{
                "connection_id": str(uuid4()) if fault == "wrong_connection"
                    else str(business_binding.connection_id),
                "operation": "logistics_query" if fault == "wrong_operation" else "order_query",
            }],
        }),
    })
    resolver = AsyncMock(return_value={workflow_id: release})
    if fault == "unpublished":
        resolver.side_effect = RuntimeError("unavailable-private-detail")
    if fault == "late_change":
        async def change_binding(*args):
            binding.release_digest = "c" * 64
            await db.commit()
            return {workflow_id: release}
        resolver.side_effect = change_binding
    monkeypatch.setattr(readiness_module.workflow_service_client, "resolve_published_bindings", resolver)

    result = await service.read(agent.project_id, actor, agent.id)

    assert result.can_enable is (fault is None)
    if fault is not None:
        assert any(item.code == "dependency_not_verified" for item in result.blockers)
    assert "unavailable-private-detail" not in result.model_dump_json()
    if fault != "unpinned":
        resolver.assert_awaited_once_with([workflow_id], str(agent.project_id), {workflow_id: 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing_action", "wrong_action", "unsafe_node",
                                   "missing_summary", "wrong_input", "owner_down"])
async def test_aftersales_workflow_needs_fixed_live_approval_summary(setup, monkeypatch, fault):
    db, agent, actor, policy, _, owner, coverage, _, service = setup
    from app.services import agent_activation_readiness as readiness_module

    body = owner.get_agent_activation_requirements.return_value.model_dump(mode="json")
    body["template_id"] = "aftersales"
    for case in body["knowledge_cases"]:
        case["template_id"] = "aftersales"
    body["requirements_revision"] = AgentActivationRequirements.model_construct(
        **body).definition_revision()
    requirements = AgentActivationRequirements.model_validate(body)
    owner.get_agent_activation_requirements.return_value = requirements
    policy.template_id = "aftersales"
    policy.requirements_revision = body["requirements_revision"]
    coverage.capabilities[0].template_id = "aftersales"
    for actual, expected in zip(coverage.capabilities[0].cases,
                                requirements.knowledge_cases):
        actual.case = expected
    workflow_id, release_id = str(uuid4()), str(uuid4())
    db.add(AgentWorkflow(agent_id=agent.id, workflow_id=workflow_id, enabled=True,
                         release_version=1, release_id=release_id,
                         release_digest="a" * 64))
    await db.commit()
    release = WorkflowIntegrationRelease.model_validate({
        "workflow_id": workflow_id, "release_version": 1,
        "release_id": release_id, "definition_digest": "a" * 64,
        "input_parameters": [{"name": "other" if fault == "wrong_input" else "question",
                              "type": "string", "required": True}],
        **({} if fault == "missing_summary" else {
            "node_types": ["input", "approval", "tool", "answer"] if fault == "unsafe_node"
                else ["input", "approval", "answer"],
            "required_business_queries": [],
            "approval_actions": [] if fault == "missing_action" else [
                "none" if fault == "wrong_action" else "create_aftersales_review"],
        }),
    })
    resolver = AsyncMock(return_value={workflow_id: release})
    if fault == "owner_down":
        resolver.side_effect = RuntimeError("private-owner-error")
    monkeypatch.setattr(readiness_module.workflow_service_client,
                        "resolve_published_bindings", resolver)
    result = await service.read(agent.project_id, actor, agent.id)
    assert result.can_enable is (fault is None)
    if fault is not None:
        assert any(blocker.code == "dependency_not_verified" for blocker in result.blockers)
    assert "private-owner-error" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["foreign_project", "stale", "owner_down"])
async def test_formal_runtime_must_be_current_and_owned(setup, fault):
    _, agent, actor, _, _, service = await business_setup(setup)
    from app.schemas.agent_business_execution import BusinessRuntimeReadiness
    result = BusinessRuntimeReadiness(project_id=agent.project_id, requested_by=actor,
        available=True,
        operations=["order_query", "logistics_query", "customer_profile_query", "membership_query"],
        checked_at=datetime.now(UTC))
    if fault == "foreign_project":
        result.project_id = uuid4()
    elif fault == "stale":
        result.checked_at = datetime.now(UTC) - timedelta(minutes=5)
    else:
        service.runtime.side_effect = RuntimeError("private-marker")
    service.runtime.return_value = result
    proof = await service.read(agent.project_id, actor, agent.id)
    assert not proof.can_enable
    assert "private-marker" not in proof.model_dump_json()


@pytest.mark.asyncio
async def test_two_current_business_reviews_are_visible_without_opening_runtime(setup):
    _, agent, actor, _, coverage, service = await business_setup(setup)
    result = await service.read(agent.project_id, actor, agent.id)
    assert result.business_evidence_revision
    assert result.channels[0].status == "passed"
    assert result.channels[0].business_capabilities == coverage.capabilities
    assert result.can_enable is False and agent.is_active is False
    assert [row.code for row in result.blockers] == ["dependency_not_verified"]
    service.knowledge.read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_logistics", "not_reviewed", "rejected", "foreign_actor", "stale"])
async def test_each_required_operation_needs_owned_current_business_evidence(setup, fault):
    _, agent, actor, _, coverage, service = await business_setup(setup)
    if fault == "missing_logistics":
        coverage.capabilities.pop()
    elif fault == "foreign_actor":
        coverage.checked_by = uuid4()
    elif fault == "stale":
        coverage.checked_at = datetime.now(UTC) - timedelta(minutes=5)
    else:
        coverage.capabilities[1].status = "review_required" if fault == "not_reviewed" else "rejected"
    result = await service.read(agent.project_id, actor, agent.id)
    assert not result.can_enable and result.channels[0].status != "passed"
    assert any(row.code == "tests_not_current" for row in result.blockers)


@pytest.mark.asyncio
async def test_business_change_during_an_owner_await_removes_earlier_green_coverage(setup):
    db, agent, actor, binding, coverage, service = await business_setup(setup)
    async def change(*args):
        binding.authorization_revision += 1
        await db.commit()
        return coverage
    service.business.read.side_effect = change
    result = await service.read(agent.project_id, actor, agent.id)
    assert not result.can_enable and result.channels[0].status == "needs_retest"
    assert any(row.code == "tests_not_current" for row in result.blockers)


@pytest.mark.asyncio
async def test_same_configuration_cannot_reuse_another_business_confirmation(setup, monkeypatch):
    _, agent, actor, _, _, _, _, _, service = setup
    proof = await service.read(agent.project_id, actor, agent.id)
    proof.business_evidence_revision = "a" * 64
    expected = ActivationExpectation(checked_at=proof.checked_at,
        configuration_fingerprint=proof.configuration_fingerprint,
        model_owner_revision=proof.model_owner_revision,
        business_evidence_revision="b" * 64,
        channels=[row.model_dump(exclude={"status", "valid_until", "business_capabilities"}) for row in proof.channels])
    monkeypatch.setattr(AgentActivationReadinessService, "read", AsyncMock(return_value=proof))
    with pytest.raises(ActivationConfirmationConflict):
        await require_activation(service.db, agent.project_id, actor, agent.id, expected=expected)


@pytest.mark.asyncio
async def test_legacy_knowledge_digest_is_preserved_but_business_revision_is_exact(setup):
    _, agent, actor, _, _, _, _, _, service = setup
    proof = await service.read(agent.project_id, actor, agent.id)
    expected = ActivationExpectation(checked_at=proof.checked_at,
        configuration_fingerprint=proof.configuration_fingerprint,
        model_owner_revision=proof.model_owner_revision,
        channels=[row.model_dump(exclude={"status", "valid_until", "business_capabilities"}) for row in proof.channels])
    command = AgentActivationRequest(request_key=uuid4(), expected=expected)
    old_payload = command.model_dump(mode="json")
    old_payload["expected"].pop("business_evidence_revision")
    old_digest = hashlib.sha256(json.dumps(old_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert command_digest(command) == old_digest
    first = command.model_copy(update={"expected": expected.model_copy(update={"business_evidence_revision": "a" * 64})})
    second = command.model_copy(update={"expected": expected.model_copy(update={"business_evidence_revision": "b" * 64})})
    assert len({command_digest(command), command_digest(first), command_digest(second)}) == 3
