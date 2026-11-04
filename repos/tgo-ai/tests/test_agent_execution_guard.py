"""A serving flag and another channel's test cannot authorize this run."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.exceptions import AuthorizationError, ConflictError
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_trial_fingerprint import configuration_fingerprint
from app.models.agent_business_connection import AgentBusinessConnection
from uuid import uuid4
from tests.test_agent_activation_readiness import setup as setup


@pytest.mark.asyncio
async def test_readiness_can_check_one_channel_without_borrowing_another(setup):
    _, agent, actor, _, _, _, _, _, readiness = setup
    valid = await readiness.read(agent.project_id, actor, agent.id, channel=KnowledgeChannel.WEB)
    assert valid.can_enable and [row.channel.value for row in valid.channels] == ["web"]
    wrong = await readiness.read(agent.project_id, actor, agent.id, channel=KnowledgeChannel.WECOM_KF)
    assert not wrong.can_enable and not wrong.channels


@pytest.mark.asyncio
async def test_current_configuration_for_the_verified_channel_is_returned(setup):
    from app.services.agent_execution_guard import AgentExecutionGuard

    db, agent, _, _, internal, _, _, _, readiness = setup
    agent.is_active = True
    await db.commit()
    load = AsyncMock(return_value=internal)
    guard = AgentExecutionGuard(db, readiness=readiness, runtime_reader=load)
    result = await guard.prepare(agent.project_id, internal, KnowledgeChannel.WEB)
    assert result == internal and configuration_fingerprint(result)
    assert load.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_channel", "other_channel", "paused", "removed", "expired", "configuration", "owner_unavailable"])
async def test_unverified_current_run_is_rejected_before_model_construction(setup, fault):
    from app.services.agent_execution_guard import AgentExecutionGuard

    db, agent, _, policy, internal, owner, coverage, _, readiness = setup
    agent.is_active = fault != "paused"
    channel = KnowledgeChannel.WEB
    if fault == "missing_channel":
        channel = None
    elif fault == "other_channel":
        channel = KnowledgeChannel.WECOM_KF
    elif fault == "removed":
        policy.deleted_at = datetime.now(UTC)
    elif fault == "expired":
        coverage.capabilities[0].valid_until = datetime.now(UTC) - timedelta(seconds=1)
    elif fault == "configuration":
        internal.instruction = "changed business configuration"
    elif fault == "owner_unavailable":
        owner.get_agent_activation_requirements.side_effect = RuntimeError("synthetic owner down")
    await db.commit()
    load = AsyncMock(return_value=internal)
    with pytest.raises((AuthorizationError, ConflictError)):
        await AgentExecutionGuard(db, readiness=readiness, runtime_reader=load).prepare(agent.project_id, internal, channel)
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_credentials_or_policy_changed_during_loading_are_rejected(setup):
    from app.services.agent_execution_guard import AgentExecutionGuard

    db, agent, _, _, internal, _, _, _, readiness = setup
    agent.is_active = True
    await db.commit()
    changed = internal.model_copy(deep=True)
    changed.model_owner_revision = "d" * 64
    with pytest.raises(ConflictError):
        await AgentExecutionGuard(db, readiness=readiness, runtime_reader=AsyncMock(return_value=changed)).prepare(agent.project_id, internal, KnowledgeChannel.WEB)


@pytest.mark.asyncio
async def test_legacy_serving_employee_only_loads_status(setup, monkeypatch):
    from app.services.agent_execution_guard import AgentExecutionGuard

    db, agent, _, policy, internal, _, _, _, readiness = setup
    await db.delete(policy)
    agent.is_active = True
    await db.commit()
    load = AsyncMock()
    load_employee_graph = AsyncMock(wraps=readiness.policies._agent)
    monkeypatch.setattr(readiness.policies, "_agent", load_employee_graph)
    guard = AgentExecutionGuard(db, readiness=readiness, runtime_reader=load)
    assert await guard.prepare(agent.project_id, internal, None) == internal
    load_employee_graph.assert_not_awaited()
    load.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["pause", "policy", "role", "business_configuration", "business_evidence"])
async def test_loading_a_runtime_cannot_hide_permission_revocation(setup, change):
    from app.services.agent_execution_guard import AgentExecutionGuard

    db, agent, actor, policy, internal, owner, _, _, readiness = setup
    agent.is_active = True
    await db.commit()

    async def load(*_):
        if change == "pause":
            agent.is_active = False
        elif change == "policy":
            policy.revision += 1
        elif change == "business_configuration":
            agent.instruction = "configuration changed during runtime loading"
        elif change == "business_evidence":
            db.add(AgentBusinessConnection(project_id=agent.project_id, agent_id=agent.id,
                connection_id=uuid4(), operations=["order_query"], enabled=True,
                revision=1, configuration_revision=1, authorization_revision=1,
                resource_revision="e" * 64, configured_by=actor))
        else:
            owner.get_agent_activation_requirements.side_effect = AuthorizationError("role revoked")
        await db.commit()
        return internal

    with pytest.raises((AuthorizationError, ConflictError)):
        await AgentExecutionGuard(db, readiness=readiness, runtime_reader=load).prepare(agent.project_id, internal, KnowledgeChannel.WEB)


@pytest.mark.asyncio
async def test_legacy_employee_with_business_binding_cannot_bypass_policy_checks(setup):
    from app.services.agent_execution_guard import AgentExecutionGuard
    db, agent, actor, policy, internal, _, _, _, readiness = setup
    await db.delete(policy)
    agent.is_active = True
    db.add(AgentBusinessConnection(project_id=agent.project_id, agent_id=agent.id,
        connection_id=uuid4(), operations=["order_query"], enabled=True,
        revision=1, configuration_revision=1, authorization_revision=1,
        resource_revision="e" * 64, configured_by=actor))
    await db.commit()
    with pytest.raises(AuthorizationError):
        await AgentExecutionGuard(db, readiness=readiness).prepare(agent.project_id, internal, None)


@pytest.mark.asyncio
async def test_real_supervisor_paths_do_not_build_a_model_for_an_untrusted_channel(setup, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app.runtime.supervisor.application import service as runtime_module
    from app.runtime.supervisor.application.service import SupervisorRuntimeService
    from app.runtime.supervisor.infrastructure.services import AIServiceClient
    from app.schemas.agent_run import SupervisorRunRequest
    from app.streaming.event_emitter import StreamingEventEmitter

    db, agent, _, _, internal, _, _, _, _ = setup
    agent.is_active = True
    await db.commit()
    runtime = SupervisorRuntimeService(session_factory=Mock(), tools_runtime_service=Mock(_settings=None))
    runtime._agent_builder.build_agent = AsyncMock()
    runtime._agent_runner.run = AsyncMock()
    runtime._agent_runner.stream = AsyncMock()

    @asynccontextmanager
    async def owned_service():
        yield SimpleNamespace(db=db)

    monkeypatch.setattr(runtime, "_agent_service_context", owned_service)
    monkeypatch.setattr(AIServiceClient, "get_agent", AsyncMock(return_value=internal))
    monkeypatch.setattr(runtime_module, "get_event_emitter", lambda request, correlation: StreamingEventEmitter(request, correlation))
    payload = SupervisorRunRequest(agent_id=str(agent.id), message="test", knowledge_channel="web", disable_tools=True)
    result = await runtime.run(payload, agent.project_id)
    assert not result.success and result.content == ""
    assert "渠道" in result.error
    response = await runtime.stream(payload, agent.project_id, http_request=SimpleNamespace(is_disconnected=AsyncMock(return_value=False)))
    async with asyncio.timeout(3):
        chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
    assert "workflow_failed" in body and "workflow_completed" not in body
    runtime._agent_builder.build_agent.assert_not_awaited()
    runtime._agent_runner.run.assert_not_awaited()
    runtime._agent_runner.stream.assert_not_awaited()
