"""Migration and competing requests in an explicitly isolated PostgreSQL DB."""

import asyncio
import importlib.util
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent import Agent as StoredAgent
from app.models.agent_test_report import AgentTestReport
from app.models.base import BaseModel
from app.models.project import Project
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.schemas.agent_run import SupervisorRunResponse
from app.schemas.agent_trials import AgentTrialRequest
from app.services.agent_trials import AgentTrialService, TrialOutcome
from tests.test_agent_knowledge_trial import employee, evidence

TEST_URL = os.getenv("AI_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="An isolated PostgreSQL test database is required")


@pytest.mark.asyncio
async def test_snapshot_upgrade_and_downgrade_preserve_historical_report():
    assert TEST_URL and (make_url(TEST_URL).database or "").startswith("tgo_scenarios_test_")
    assert make_url(TEST_URL).host in {"localhost", "127.0.0.1"}
    engine = create_async_engine(TEST_URL)
    schema = "trial_snapshot_" + uuid4().hex
    modules = []
    for filename in ["r0s1t2u3v4w5_agent_test_reports.py", "t2u3v4w5x6y7_trial_knowledge_snapshots.py"]:
        path = Path(__file__).parents[1] / "migrations/versions" / filename
        spec = importlib.util.spec_from_file_location(filename, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                def apply(sync, module, action):
                    with Operations.context(MigrationContext.configure(sync)):
                        getattr(module, action)()
                await connection.run_sync(apply, modules[0], "upgrade")
                identity = uuid4()
                await connection.execute(text("""INSERT INTO ai_agent_test_reports
                    (id, project_id, created_by, agent_id, request_key, request_digest, status,
                     channel, question, answer, sources, expires_at)
                    VALUES (:id, :project, :creator, :agent, :key, :digest, 'completed',
                            'web', '旧问题', '历史回答', '[]'::jsonb, now())"""),
                    {"id": identity, "project": uuid4(), "creator": uuid4(), "agent": uuid4(),
                     "key": uuid4(), "digest": "a" * 64})
                await connection.run_sync(apply, modules[1], "upgrade")
                row = (await connection.execute(text("SELECT answer, knowledge_snapshot FROM ai_agent_test_reports"))).one()
                assert row.answer == "历史回答" and row.knowledge_snapshot is None
                await connection.execute(text("UPDATE ai_agent_test_reports SET knowledge_snapshot='{}'::jsonb"))
                await connection.run_sync(apply, modules[1], "downgrade")
                assert (await connection.execute(text("SELECT answer FROM ai_agent_test_reports"))).scalar_one() == "历史回答"
                await connection.run_sync(apply, modules[1], "upgrade")
                assert (await connection.execute(text("SELECT knowledge_snapshot FROM ai_agent_test_reports"))).scalar_one() is None
            finally:
                await transaction.rollback()
            assert not (await connection.execute(text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:name)"),
                                                {"name": schema})).scalar_one()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_trial_migration_duplicate_claim_and_cancel_are_atomic(monkeypatch):
    assert TEST_URL and (make_url(TEST_URL).database or "").startswith("tgo_scenarios_test_")
    schema = "trial_test_" + uuid4().hex
    admin = create_async_engine(TEST_URL)
    engine = create_async_engine(TEST_URL, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    path = Path(__file__).parents[1] / "migrations/versions/r0s1t2u3v4w5_agent_test_reports.py"
    spec = importlib.util.spec_from_file_location("trial_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    created = False
    released, started = asyncio.Event(), asyncio.Event()
    jobs = []
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True

        def upgrade(connection):
            tables = [value for value in BaseModel.metadata.sorted_tables if value is not AgentTestReport.__table__]
            BaseModel.metadata.create_all(connection, tables=tables)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                snapshot_path = Path(__file__).parents[1] / "migrations/versions/t2u3v4w5x6y7_trial_knowledge_snapshots.py"
                snapshot_spec = importlib.util.spec_from_file_location("snapshot_migration", snapshot_path)
                snapshot_migration = importlib.util.module_from_spec(snapshot_spec)
                snapshot_spec.loader.exec_module(snapshot_migration)
                snapshot_migration.upgrade()

        async with engine.begin() as connection:
            await connection.run_sync(upgrade)
        project, staff, identifier = uuid4(), uuid4(), uuid4()
        internal = employee().model_copy(update={"id": identifier, "project_id": str(project)})
        monkeypatch.setattr(AIServiceClient, "get_trial_agent", AsyncMock(return_value=internal))
        from app.schemas.agent_trials import TrialKnowledgeSnapshot
        from app.services import agent_trial_dependencies, agent_trials
        snapshot = TrialKnowledgeSnapshot(channel="web", binding_mode="unbound", collections=[],
            issues=["no_binding"], captured_at=internal.created_at)
        lookup = AsyncMock(return_value=snapshot)
        monkeypatch.setattr(agent_trials, "read_knowledge_snapshot", lookup)
        monkeypatch.setattr(agent_trial_dependencies, "read_knowledge_snapshot", lookup)

        async def generate(*args):
            started.set()
            await released.wait()
            return SupervisorRunResponse(
                success=True, message="completed", content="已完成试聊",
                knowledge_evidence=evidence(internal),
            )

        runtime = Mock(run_knowledge_trial=AsyncMock(side_effect=generate))
        async with sessions() as db:
            db.add(Project(id=project, name="隔离试聊", api_key=f"ak_{uuid4().hex}"))
            db.add(StoredAgent(
                id=identifier, project_id=project, name="待启用员工", model="test",
                is_active=False, tools=[], workflows=[], collections=[],
            ))
            await db.commit()
        request = AgentTrialRequest(message="材质？", request_key=uuid4())

        async def submit():
            async with sessions() as db:
                return await AgentTrialService(db, runtime).start(project, staff, identifier, request)

        jobs = [asyncio.create_task(submit()), asyncio.create_task(submit())]
        await asyncio.wait_for(started.wait(), 10)
        done, _ = await asyncio.wait(jobs, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        assert len(done) == 1
        duplicate = next(iter(done)).result()
        assert duplicate.status == "running"
        runtime.run_knowledge_trial.assert_awaited_once()
        released.set()
        reports = await asyncio.wait_for(asyncio.gather(*jobs), 10)
        assert len({value.id for value in reports}) == 1
        assert sorted(value.status for value in reports) == ["completed", "running"]
        async with sessions() as db:
            assert len((await db.scalars(select(AgentTestReport))).all()) == 1
            assert (await db.get(StoredAgent, identifier)).is_active is False

        # Cancel through a different transaction, then simulate a late writer.
        released.clear()
        started.clear()
        cancelled_request = request.model_copy(update={"request_key": uuid4()})

        async def submit_cancelled():
            async with sessions() as db:
                return await AgentTrialService(db, runtime).start(project, staff, identifier, cancelled_request)

        task = asyncio.create_task(submit_cancelled())
        jobs.append(task)
        await asyncio.wait_for(started.wait(), 10)
        async with sessions() as db:
            service = AgentTrialService(db, runtime)
            pending = await service.by_request(project, staff, identifier, cancelled_request.request_key)
            assert (await service.cancel(project, staff, identifier, pending.id)).status == "cancelled"
            await service._finish(pending.id, TrialOutcome(status="completed", answer="late"))
        result = await asyncio.wait_for(task, 10)
        assert result.status == "cancelled" and result.answer == ""

        def downgrade(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()

        async with engine.begin() as connection:
            await connection.run_sync(downgrade)
            assert (await connection.execute(text(
                "SELECT is_active FROM ai_agents WHERE id=:identifier"
            ), {"identifier": identifier})).scalar_one() is False
    finally:
        released.set()
        for job in jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
