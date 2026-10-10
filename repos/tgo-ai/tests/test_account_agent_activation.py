"""Exercise account activation through the real service and database."""
from uuid import uuid4

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

        async def without_external_enrichment(project_id, agent_id):
            return await original_get(
                project_id, agent_id, enrich_resources=False
            )

        service.get_agent = without_external_enrichment
        yield db, service, project.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_activation_switches_default_and_preserves_other_accounts(
    account_database,
):
    db, service, project = account_database
    other = uuid4()
    old = await service.create_agent(
        project, AgentCreate(name="old", model="fixture", is_default=True)
    )
    foreign = await service.create_agent(
        other, AgentCreate(name="foreign", model="fixture", is_default=True)
    )
    candidate = await service.create_agent(
        project, AgentCreate(name="new", model="fixture", is_active=False)
    )
    old_instruction = old.instruction
    activated = await service.update_agent(
        project, candidate.id, AgentUpdate(is_active=True)
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
async def test_active_creation_also_switches_existing_employee(
    account_database,
):
    db, service, project = account_database
    old = await service.create_agent(
        project, AgentCreate(name="old", model="fixture", is_default=True)
    )
    new = await service.create_agent(
        project,
        AgentCreate(
            name="new", model="fixture", is_active=True, is_default=False
        ),
    )
    await db.refresh(old)
    assert new.is_active and new.is_default
    assert not old.is_active and not old.is_default


@pytest.mark.asyncio
async def test_disabling_and_editing_do_not_activate_another_employee(
    account_database,
):
    db, service, project = account_database
    active = await service.create_agent(
        project, AgentCreate(name="active", model="fixture", is_default=True)
    )
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
    await service.create_agent(
        project, AgentCreate(name="active", model="fixture")
    )
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
    old = await service.create_agent(
        project,
        AgentCreate(name="paused", model="fixture", is_active=False,
                    is_default=True),
    )
    active = await service.create_agent(
        project,
        AgentCreate(name="enabled", model="fixture", is_active=False),
    )
    # Reproduce a pre-migration account without rewriting its preferences.
    active.is_active = True
    await db.commit()
    foreign = await service.create_agent(
        uuid4(), AgentCreate(name="foreign", model="fixture"),
    )
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
    _, service, project = account_database
    paused = await service.create_agent(
        project,
        AgentCreate(name="paused", model="fixture", is_active=False,
                    is_default=True),
    )
    selected = await service.get_default_agent(
        project, enrich_resources=False,
    )
    assert selected.id == paused.id and not selected.is_active
