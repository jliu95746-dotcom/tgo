"""Real default migration and concurrent activation transactions on isolated PostgreSQL."""

import asyncio
import importlib.util
import os
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.exceptions import ConflictError
from app.models import Agent, BaseModel
from app.schemas.agent import AgentUpdate
from app.services.agent_activation_readiness import AgentActivationReadinessService
from app.services.agent_service import AgentService
from tests.test_account_agent_activation import proof

TEST_URL = os.getenv("AI_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="An isolated PostgreSQL database is required")


async def insert_without_activation(connection, project, name):
    identifier = uuid4()
    await connection.execute(text("INSERT INTO ai_agents "
        "(id, project_id, name, model, is_default, is_remote_store_agent, agent_category, skills_enabled) "
        "VALUES (:id, :project, :name, 'fixture', false, false, 'normal', false)"),
        {"id": identifier, "project": project, "name": name})
    return identifier


@pytest.mark.asyncio
async def test_default_migration_atomic_switches_rejection_and_legacy_preservation(monkeypatch):
    assert TEST_URL
    url = make_url(TEST_URL)
    assert url.host in {"127.0.0.1", "localhost"} and url.port == 15432
    assert (url.database or "").startswith("tgo_scenarios_test_")
    schema = "activation_guard_" + uuid4().hex
    admin = create_async_engine(TEST_URL)
    engine = create_async_engine(TEST_URL, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    path = Path(__file__).parents[1] / "migrations/versions/w5x6y7z8a9b0_new_agents_paused.py"
    spec = importlib.util.spec_from_file_location("new_agent_default_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    project, actor, serving, first, second, rejected = [uuid4() for _ in range(6)]
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        async with engine.begin() as connection:
            await connection.run_sync(BaseModel.metadata.create_all)
            await connection.execute(text("ALTER TABLE ai_agents ALTER COLUMN is_active SET DEFAULT true"))
            legacy = await insert_without_activation(connection, uuid4(), "迁移前存量员工")
            assert (await connection.execute(text("SELECT is_active FROM ai_agents WHERE id=:id"), {"id": legacy})).scalar_one() is True
            def upgrade(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
            await connection.run_sync(upgrade)
            paused = await insert_without_activation(connection, uuid4(), "迁移后新员工")
            assert (await connection.execute(text("SELECT is_active FROM ai_agents WHERE id=:id"), {"id": paused})).scalar_one() is False
            assert (await connection.execute(text("SELECT is_active FROM ai_agents WHERE id=:id"), {"id": legacy})).scalar_one() is True
        async with sessions() as db:
            db.add_all([
                Agent(id=serving, project_id=project, name="正在接待", model="fixture", is_active=True, is_default=True),
                Agent(id=first, project_id=project, name="已测试一", model="fixture", is_active=False, skills_enabled=False),
                Agent(id=second, project_id=project, name="已测试二", model="fixture", is_active=False, skills_enabled=False),
                Agent(id=rejected, project_id=project, name="未测试修改", model="fixture", instruction="原职责", is_active=False),
            ])
            await db.commit()
        async def current(project_id, actor_id, target):
            await asyncio.sleep(0.05)
            result = proof(project_id, actor_id, target)
            if target == rejected:
                result.can_enable = False
            return result
        monkeypatch.setattr(AgentActivationReadinessService, "read", AsyncMock(side_effect=current))
        async def activate(target):
            async with sessions() as db:
                return (await AgentService(db).update_agent(project, target, AgentUpdate(is_active=True),
                    requested_by=actor, enrich_resources=False)).id
        assert set(await asyncio.gather(activate(first), activate(second))) == {first, second}
        async with sessions() as db:
            enabled = list((await db.scalars(select(Agent).where(Agent.project_id == project,
                Agent.is_active.is_(True)))).all())
            assert len(enabled) == 1 and enabled[0].is_default
            active = enabled[0].id
            with pytest.raises(ConflictError):
                await AgentService(db).update_agent(project, rejected,
                    AgentUpdate(is_active=True, instruction="未经测试的新职责"), requested_by=actor, enrich_resources=False)
            await db.rollback()
        async with sessions() as db:
            assert (await db.get(Agent, active)).is_active
            assert (await db.get(Agent, rejected)).instruction == "原职责"
            assert not (await db.get(Agent, rejected)).is_active
        async with engine.begin() as connection:
            def downgrade(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            await connection.run_sync(downgrade)
            restored = await insert_without_activation(connection, uuid4(), "回退后新行")
            assert (await connection.execute(text("SELECT is_active FROM ai_agents WHERE id=:id"), {"id": restored})).scalar_one() is True
            assert (await connection.execute(text("SELECT is_active FROM ai_agents WHERE id=:id"), {"id": paused})).scalar_one() is False
            await connection.run_sync(upgrade)
    finally:
        await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
