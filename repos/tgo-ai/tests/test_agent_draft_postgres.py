"""Real independent transactions prove one durable employee per setup draft."""
import asyncio
import importlib.util
import os
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.exceptions import ConflictError, NotFoundError
from app.models.agent import Agent
from app.models.agent_draft_binding import AgentDraftBinding
from app.models.base import BaseModel
from app.models.project import Project
from app.schemas.agent_drafts import AgentDraftRequest
from app.services.agent_drafts import AgentDraftService

TEST_URL = os.getenv("AI_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="An isolated PostgreSQL test database is required")


@pytest.mark.asyncio
async def test_atomic_save_retry_edit_ownership_and_migration(monkeypatch):
    assert TEST_URL and (make_url(TEST_URL).database or "").startswith("tgo_scenarios_test_")
    schema = "agent_setup_" + uuid4().hex
    admin = create_async_engine(TEST_URL)
    engine = create_async_engine(TEST_URL, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    migration_path = Path(__file__).parents[1] / "migrations/versions/s1t2u3v4w5x6_agent_draft_bindings.py"
    spec = importlib.util.spec_from_file_location("draft_binding_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True

        def upgrade(connection):
            BaseModel.metadata.create_all(connection, tables=[
                table for table in BaseModel.metadata.sorted_tables if table is not AgentDraftBinding.__table__
            ])
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        async with engine.begin() as connection:
            await connection.run_sync(upgrade)

        project, staff, draft, serving = uuid4(), uuid4(), uuid4(), uuid4()
        async with sessions() as db:
            db.add(Project(id=project, name="隔离草稿测试", api_key="synthetic_" + uuid4().hex))
            db.add(Agent(id=serving, project_id=project, name="正在服务员工", model="test",
                         is_active=True, is_default=True))
            await db.commit()
        request = AgentDraftRequest.model_validate({"revision": 1, "agent": {
            "name": "澜月客服", "model": "__system_default__", "instruction": "按资料回答",
            "is_active": False, "is_default": False, "config": {"profession": "商品咨询"},
        }})
        async def save(payload=request):
            async with sessions() as db:
                return await AgentDraftService(db).save(project, staff, draft, payload)
        receipts = await asyncio.wait_for(asyncio.gather(save(), save()), 20)
        assert receipts[0] == receipts[1]
        identifier = receipts[0].agent_id
        assert receipts[0].is_active is False and receipts[0].can_enable is False
        async with sessions() as db:
            assert len((await db.scalars(select(AgentDraftBinding))).all()) == 1
            assert len((await db.scalars(select(Agent))).all()) == 2
            assert (await db.get(Agent, serving)).is_active is True
            assert await AgentDraftService(db).get(project, staff, draft) == receipts[0]
            for p, s in [(uuid4(), staff), (project, uuid4())]:
                with pytest.raises(NotFoundError):
                    await AgentDraftService(db).get(p, s, draft)
            with pytest.raises(NotFoundError):
                await AgentDraftService(db).save(project, uuid4(), draft, request)
        edited = request.model_copy(deep=True)
        edited.revision = 2
        edited.agent.name = "澜月售后客服"
        updated = await save(edited)
        assert updated.agent_id == identifier and updated.revision == 2
        async with sessions() as db:
            assert (await db.get(Agent, identifier)).name == edited.agent.name
            assert (await db.get(Agent, serving)).is_active is True
        with pytest.raises(ConflictError):
            await save(request)
        collision = edited.model_copy(deep=True)
        collision.agent.instruction = "不同的同版本内容"
        with pytest.raises(ConflictError):
            await save(collision)

        # A committed setup may later be edited in the ordinary employee page.
        # Retrying its old snapshot must not silently accept that different config.
        async with sessions() as db:
            employee = await db.get(Agent, identifier)
            employee.instruction = "另一页面修改后的职责"
            await db.commit()
        with pytest.raises(ConflictError):
            await save(edited)
        edited.revision = 3
        assert (await save(edited)).agent_id == identifier

        # Creation and the setup link either both commit or both roll back.
        async with sessions() as db:
            monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("synthetic commit failure")))
            with pytest.raises(RuntimeError):
                await AgentDraftService(db).save(project, staff, uuid4(), request)
        async with sessions() as db:
            assert len((await db.scalars(select(Agent))).all()) == 2
            assert len((await db.scalars(select(AgentDraftBinding))).all()) == 1

        # Tombstones survive soft deletion and retries cannot resurrect them.
        async with sessions() as db:
            employee = await db.get(Agent, identifier)
            employee.soft_delete()
            await db.commit()
        with pytest.raises(ConflictError):
            await save(edited)
        async with engine.begin() as connection:
            def downgrade(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            await connection.run_sync(downgrade)
            assert (await connection.execute(text("SELECT count(*) FROM ai_agents"))).scalar_one() == 2
            assert (await connection.execute(text("SELECT is_active FROM ai_agents WHERE id=:id"), {"id": serving})).scalar_one() is True
            def reupgrade(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
            await connection.run_sync(reupgrade)
    finally:
        await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
