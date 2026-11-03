"""Exercise account activation through the real service and database."""
from uuid import uuid4
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.models.agent import Agent
from app.models.base import BaseModel
from app.models.project import Project
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services.agent_service import AgentService
from app.services.agent_activation_readiness import AgentActivationReadinessService
from app.schemas.agent_activation_readiness import AgentActivationReadiness
from app.exceptions import ConflictError


@compiles(JSONB, "sqlite")
def sqlite_json_type(element, compiler, **kwargs):
    return "JSON"


@pytest_asyncio.fixture
async def account_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        project = Project(
            id=uuid4(), name="activation fixture", api_key=uuid4().hex
        )
        db.add(project)
        await db.commit()
        service = AgentService(db)
        original_get = service.get_agent

        async def without_external_enrichment(project_id, agent_id, **kwargs):
            return await original_get(
                project_id, agent_id, enrich_resources=False
            )

        service.get_agent = without_external_enrichment
        yield db, service, project.id
    await engine.dispose()


async def legacy_agent(db, project, name, *, active=True, default=True):
    agent = Agent(project_id=project, name=name, model="fixture", is_active=active,
        is_default=default, skills_enabled=False)
    db.add(agent)
    await db.commit()
    return agent


def proof(project, actor, agent):
    return AgentActivationReadiness(project_id=project, agent_id=agent, checked_by=actor,
        checked_at=datetime.now(UTC), enabled=False, can_enable=True,
        configuration_fingerprint="a" * 64, model_owner_revision="b" * 64,
        channels=[{"template_id": "knowledge", "template_version": 1, "channel": "web",
            "policy_revision": 1, "requirements_revision": "c" * 64, "status": "passed",
            "valid_until": datetime.now(UTC) + timedelta(hours=1)}])


@pytest.mark.asyncio
async def test_activation_switches_default_and_preserves_other_accounts(
    account_database, monkeypatch,
):
    db, service, project = account_database
    other = uuid4()
    old = await legacy_agent(db, project, "old")
    foreign = await legacy_agent(db, other, "foreign")
    candidate = await service.create_agent(
        project, AgentCreate(name="new", model="fixture", is_active=False)
    )
    old_instruction = old.instruction
    actor = uuid4()
    monkeypatch.setattr(AgentActivationReadinessService, "read", AsyncMock(return_value=proof(project, actor, candidate.id)))
    activated = await service.update_agent(
        project, candidate.id, AgentUpdate(is_active=True), requested_by=actor,
    )
    assert activated.is_active and activated.is_default
    await db.refresh(old)
    await db.refresh(foreign)
    assert not old.is_active and not old.is_default
    assert old.instruction == old_instruction
    assert foreign.is_active and foreign.is_default
    assert (
        len(
            (
                await db.scalars(
                    select(Agent).where(
                        Agent.project_id == project,
                        Agent.is_active.is_(True),
                        Agent.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        == 1
    )


@pytest.mark.asyncio
async def test_active_creation_is_rejected_without_switching_existing_employee(
    account_database,
):
    db, service, project = account_database
    old = await legacy_agent(db, project, "old")
    with pytest.raises(ConflictError):
        await service.create_agent(project, AgentCreate(name="new", model="fixture", is_active=True))
    await db.refresh(old)
    assert old.is_active and old.is_default
    assert not await db.scalar(select(Agent.id).where(Agent.name == "new"))


@pytest.mark.asyncio
async def test_disabling_and_editing_do_not_activate_another_employee(
    account_database,
):
    db, service, project = account_database
    active = await legacy_agent(db, project, "active")
    inactive = await service.create_agent(
        project, AgentCreate(name="inactive", model="fixture", is_active=False)
    )
    await service.update_agent(
        project, inactive.id, AgentUpdate(name="edited")
    )
    await db.refresh(active)
    assert active.is_active
    assert not inactive.is_active
    await service.update_agent(
        project, active.id, AgentUpdate(is_active=False)
    )
    await db.refresh(inactive)
    assert not inactive.is_active
    assert not (
        await db.scalars(
            select(Agent).where(
                Agent.project_id == project,
                Agent.is_active.is_(True),
                Agent.deleted_at.is_(None),
            )
        )
    ).all()


@pytest.mark.asyncio
async def test_database_rejects_a_second_active_employee(account_database):
    db, service, project = account_database
    await legacy_agent(db, project, "active")
    db.add(
        Agent(
            project_id=project,
            name="bypass",
            model="fixture",
            is_active=True,
            is_default=False,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_routing_prefers_enabled_employee_over_legacy_default(
    account_database,
):
    db, service, project = account_database
    old = await legacy_agent(db, project, "paused", active=False)
    active = await service.create_agent(
        project,
        AgentCreate(name="enabled", model="fixture", is_active=False),
    )
    # Reproduce a pre-migration account without rewriting its preferences.
    active.is_active = True
    await db.commit()
    foreign = await legacy_agent(db, uuid4(), "foreign")
    selected = await service.get_default_agent(
        project, enrich_resources=False,
    )
    defaults, count = await service.list_agents(
        project, is_default=True, enrich_resources=False,
    )
    others, _ = await service.list_agents(
        project, is_default=False, enrich_resources=False,
    )
    assert selected.id == active.id
    assert count == 1 and [a.id for a in defaults] == [active.id]
    assert [a.id for a in others] == [old.id]
    await db.refresh(old)
    await db.refresh(active)
    await db.refresh(foreign)
    assert old.is_default and not old.is_active
    assert active.is_active and not active.is_default
    assert foreign.is_active
    assert (await service.get_agent(project, old.id)).id == old.id
    from app.runtime.supervisor.infrastructure.services import _convert_agent

    assert _convert_agent(selected).is_default


@pytest.mark.asyncio
async def test_all_paused_retains_default_for_readiness(account_database):
    db, service, project = account_database
    paused = await legacy_agent(db, project, "paused", active=False)
    selected = await service.get_default_agent(
        project, enrich_resources=False,
    )
    assert selected.id == paused.id and not selected.is_active
