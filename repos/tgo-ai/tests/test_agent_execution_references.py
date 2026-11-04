"""Policy revisions are fenced even when a newly valid configuration is identical."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.services.agent_execution_reference import policy_revision, reference_for
from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
from app.schemas.knowledge import KnowledgeChannel

pytest_plugins = ("tests.test_agent_activation_readiness",)


@pytest.mark.asyncio
async def test_policy_change_does_not_authorize_an_old_run_even_with_same_configuration(setup):
    db, agent, _, policy, internal, _, _, _, _ = setup
    old = await reference_for(db, agent.project_id, internal, KnowledgeChannel.WEB)
    policy.revision += 1
    await db.commit()
    fresh = await reference_for(db, agent.project_id, internal, KnowledgeChannel.WEB)
    assert old.configuration_fingerprint == fresh.configuration_fingerprint
    assert old.policy_revision != fresh.policy_revision
    async with track_execution_scope(old, AsyncMock(return_value=fresh)) as scope:
        with pytest.raises(ExecutionRevoked):
            await scope.check()


@pytest.mark.asyncio
async def test_policy_retirement_has_a_distinct_immutable_history_fence(setup):
    db, agent, _, policy, internal, _, _, _, _ = setup
    old = await reference_for(db, agent.project_id, internal, KnowledgeChannel.WEB)
    policy.deleted_at = datetime.now(UTC)
    await db.commit()
    fresh = await reference_for(db, agent.project_id, internal, KnowledgeChannel.WEB)
    assert old.policy_revision != fresh.policy_revision
    # Removing the row entirely cannot make the old managed identity match legacy.
    assert fresh.policy_revision != policy_revision([], KnowledgeChannel.WEB)


@pytest.mark.asyncio
async def test_read_owner_uses_a_fresh_transaction_and_rechecks_actual_serving_state(setup, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.services import agent_execution_reference as module
    from app.services.agent_execution_guard import AgentExecutionGuard
    from app.services.agent_activation_readiness import AgentActivationReadinessService
    from app.runtime.supervisor.infrastructure.services import AIServiceClient
    from app.exceptions import AuthorizationError
    db, agent, _, policy, internal, owner, _, configuration, readiness = setup
    agent.is_active = True
    await db.commit()
    expected = await reference_for(db, agent.project_id, internal, KnowledgeChannel.WEB)

    def guard(session):
        return AgentExecutionGuard(session, readiness=AgentActivationReadinessService(session, owner=owner,
            configuration_reader=configuration, knowledge=readiness.knowledge), runtime_reader=AsyncMock(return_value=internal))

    monkeypatch.setattr(module, "AgentExecutionGuard", guard)
    monkeypatch.setattr(AIServiceClient, "get_agent", AsyncMock(return_value=internal))
    reader = module.AgentExecutionReferenceReader(async_sessionmaker(db.bind, expire_on_commit=False))
    assert await reader(expected) == expected
    # An independent committed mutation must not be hidden by the earlier Session.
    agent.is_active = False
    await db.commit()
    with pytest.raises(AuthorizationError):
        await reader(expected)
