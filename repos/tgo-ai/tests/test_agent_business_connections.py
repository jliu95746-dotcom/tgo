"""Explicit business bindings remain configuration, not execution authority."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.exceptions import AuthorizationError, ConflictError, ExternalServiceError, NotFoundError
from app.models import Agent, BaseModel
from app.schemas.agent_business_connections import AgentBusinessConnectionWrite, BusinessConnectionOwner
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.services.agent_business_connections import AgentBusinessConnectionService


@pytest_asyncio.fixture
async def setup(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'business.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        project, actor, agent, serving, connection = [uuid4() for _ in range(5)]
        db.add_all([
            Agent(id=agent, project_id=project, name="订单客服", model="test", is_active=False),
            Agent(id=serving, project_id=project, name="接待中", model="test", is_active=True),
        ])
        await db.commit()
        owner = AsyncMock()
        owner.get_agent_configuration_actor.side_effect = lambda q: AgentConfigurationActor(
            **q.model_dump(), checked_at=datetime.now(UTC))
        owner.get_agent_business_connection.side_effect = lambda q: BusinessConnectionOwner(
            **q.model_dump(), name="澜月连接", mode="http", enabled=False,
            operations=["order_query", "logistics_query"], revision=1,
            configuration_revision=1, authorization_revision=1, resource_revision="a" * 64,
            configuration_tests=[{"operation":op,"report_id":None,"status":"not_tested","valid_until":None,"issues":[]} for op in ["order_query","logistics_query"]],
            test_status="not_tested", checked_at=datetime.now(UTC))
        yield db, AgentBusinessConnectionService(db, owner), owner, project, actor, agent, serving, connection
    await engine.dispose()


def command(revision=0, enabled=True, request_id=None):
    return AgentBusinessConnectionWrite(expected_revision=revision, request_id=request_id or uuid4(),
                                        operations=["order_query"], enabled=enabled)


@pytest.mark.asyncio
async def test_save_original_receipt_survives_later_edit_without_reapplying(setup):
    db, service, _, project, actor, agent, _, connection = setup
    first_command = command()
    first = await service.save(project, actor, agent, connection, first_command)
    second = await service.save(project, actor, agent, connection, command(1, False))
    assert first.revision == 1 and second.revision == 2 and not second.enabled
    recovered = await service.save(project, actor, agent, connection, first_command)
    assert recovered == first
    assert await service.request_result(project, actor, agent, first_command.request_id) == first
    assert (await service.list(project, actor, agent)).bindings[0] == second
    assert not first.can_enable and not (await db.get(Agent, agent)).is_active


@pytest.mark.asyncio
async def test_request_key_cannot_change_command_or_agent(setup):
    _, service, _, project, actor, agent, serving, connection = setup
    write = command()
    await service.save(project, actor, agent, connection, write)
    with pytest.raises(ConflictError):
        await service.save(project, actor, agent, connection, command(0, False, write.request_id))
    with pytest.raises(ConflictError):
        await service.save(project, actor, serving, connection, write)


@pytest.mark.asyncio
async def test_active_foreign_and_stale_changes_are_rejected(setup):
    _, service, _, project, actor, agent, serving, connection = setup
    with pytest.raises(ConflictError):
        await service.save(project, actor, serving, connection, command())
    with pytest.raises(NotFoundError):
        await service.save(uuid4(), actor, agent, connection, command())
    await service.save(project, actor, agent, connection, command())
    with pytest.raises(ConflictError):
        await service.save(project, actor, agent, connection, command())


@pytest.mark.asyncio
async def test_revoked_actor_cannot_read_recover_or_disable(setup):
    _, service, owner, project, actor, agent, _, connection = setup
    write = command()
    await service.save(project, actor, agent, connection, write)
    owner.get_agent_configuration_actor.side_effect = AuthorizationError("撤权")
    for action in [service.list(project, actor, agent),
                   service.request_result(project, actor, agent, write.request_id),
                   service.save(project, actor, agent, connection, command(1, False))]:
        with pytest.raises(AuthorizationError):
            await action


@pytest.mark.asyncio
async def test_unavailable_connection_can_be_retired_without_owner_lookup(setup):
    _, service, owner, project, actor, agent, _, connection = setup
    await service.save(project, actor, agent, connection, command())
    owner.get_agent_business_connection.side_effect = ExternalServiceError("tgo-api", "连接下线")
    removed = await service.save(project, actor, agent, connection, command(1, False))
    assert not removed.enabled and removed.revision == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["identity", "operation", "demo", "stale"])
async def test_owner_scope_capability_and_freshness_must_match(setup, kind):
    _, service, owner, project, actor, agent, _, connection = setup
    original = owner.get_agent_business_connection.side_effect
    def bad(query):
        result = original(query)
        if kind == "identity": return result.model_copy(update={"project_id": uuid4()})
        if kind == "operation": return result.model_copy(update={"operations": ["logistics_query"]})
        if kind == "demo": return result.model_copy(update={"mode": "demo"})
        return result.model_copy(update={"checked_at": datetime(2020, 1, 1, tzinfo=UTC)})
    owner.get_agent_business_connection.side_effect = bad
    with pytest.raises((ConflictError, ExternalServiceError)):
        await service.save(project, actor, agent, connection, command())
    assert not (await service.list(project, actor, agent)).bindings


@pytest.mark.asyncio
async def test_business_binding_cannot_pass_the_knowledge_activation_gate(setup):
    from app.services.agent_activation_readiness import AgentActivationReadinessService
    db, service, owner, project, actor, agent, _, connection = setup
    stored = await db.get(Agent, agent)
    stored.skills_enabled = False
    await db.commit()
    readiness = AgentActivationReadinessService(db, owner=owner)
    assert not await readiness._unverified_dependencies(stored)
    await service.save(project, actor, agent, connection, command())
    assert await readiness._unverified_dependencies(stored)
    await service.save(project, actor, agent, connection, command(1, False))
    assert not await readiness._unverified_dependencies(stored)


def test_fingerprint_preserves_empty_legacy_agents_and_tracks_binding_revisions():
    from app.models.internal import Agent as RuntimeAgent
    from app.schemas.agent_business_connections import AgentBusinessConnectionRecord
    from app.services.agent_trial_fingerprint import configuration_fingerprint, private_digest
    now=datetime.now(UTC)
    agent=RuntimeAgent(id=uuid4(),name="员工",model="fixture",created_at=now,updated_at=now)
    old=agent.model_dump(mode="json",exclude={"name","created_at","updated_at","is_default","business_connections"})
    assert configuration_fingerprint(agent)==private_digest(old)
    first=configuration_fingerprint(agent)
    binding=AgentBusinessConnectionRecord(id=uuid4(),project_id=uuid4(),agent_id=agent.id,connection_id=uuid4(),
        operations=["order_query"],enabled=True,revision=1,configuration_revision=1,
        authorization_revision=1,resource_revision="a"*64,configured_by=uuid4())
    agent.business_connections=[binding]
    second=configuration_fingerprint(agent)
    assert first!=second
    agent.business_connections=[binding.model_copy(update={"revision":2})]
    assert configuration_fingerprint(agent)!=second
