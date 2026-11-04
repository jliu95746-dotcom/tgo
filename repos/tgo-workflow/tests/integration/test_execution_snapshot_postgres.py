"""Opt-in migration and HTTP checks against an isolated PostgreSQL database."""

import asyncio
import os
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.execution import WorkflowExecution
from app.models.workflow import Workflow
from celery_app import tasks

TEST_URL = os.getenv("WORKFLOW_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="Isolated PostgreSQL test database is not configured")


@pytest.fixture
def migrated_database(monkeypatch):
    assert TEST_URL is not None
    assert (make_url(TEST_URL).database or "").startswith("tgo_scenarios_test_")
    monkeypatch.setattr(settings, "DATABASE_URL", TEST_URL)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "0001_init")
    yield config
    command.upgrade(config, "head")


def test_upgrade_and_downgrade_preserve_historical_rows(migrated_database):
    async def insert_history():
        engine = create_async_engine(TEST_URL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("""
                    INSERT INTO wf_workflows (id, project_id, name, definition, status, version, tags)
                    VALUES ('history-workflow', 'owned-project', '历史流程', '{}', 'draft', 1, '{}')
                    ON CONFLICT (id) DO NOTHING
                """))
                await connection.execute(text("""
                    INSERT INTO wf_workflow_executions (id, workflow_id, project_id, status)
                    VALUES ('history-execution', 'history-workflow', 'owned-project', 'completed')
                    ON CONFLICT (id) DO NOTHING
                """))
        finally:
            await engine.dispose()

    async def check_history(include_snapshots):
        engine = create_async_engine(TEST_URL)
        try:
            async with engine.connect() as connection:
                assert (await connection.execute(text(
                    "SELECT status FROM wf_workflow_executions WHERE id='history-execution'",
                ))).scalar_one() == "completed"
                if include_snapshots:
                    assert (await connection.execute(text(
                        "SELECT definition_snapshot FROM wf_workflow_executions WHERE id='history-execution'",
                    ))).scalar_one() is None
        finally:
            await engine.dispose()

    asyncio.run(insert_history())
    command.upgrade(migrated_database, "head")
    asyncio.run(check_history(True))
    command.downgrade(migrated_database, "0001_init")
    asyncio.run(check_history(False))


def test_http_accepts_snapshot_without_returning_private_definition(migrated_database, monkeypatch):
    command.upgrade(migrated_database, "head")

    async def verify():
        engine = create_async_engine(TEST_URL)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        definition = {
            "nodes": [
                {"id": "input", "type": "input", "data": {"reference_key": "input"}},
                {"id": "answer", "type": "answer", "data": {
                    "reference_key": "answer", "output_type": "template", "output_template": "accepted {{input.question}}",
                }},
            ],
            "edges": [{"source": "input", "target": "answer"}],
            "private_fixture": "private-snapshot-value",
        }
        async def database():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        monkeypatch.setattr(tasks.execute_workflow_task, "apply_async", Mock())
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        try:
            async with sessions() as db:
                row = await db.get(Workflow, "snapshot-http")
                if row is None:
                    row = Workflow(id="snapshot-http", project_id="owned-project", name="快照验收")
                    db.add(row)
                row.definition = definition
                row.status = "draft"
                row.version = 2
                row.tags = []
                await db.commit()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                capabilities = await client.get("/v1/workflows/capabilities")
                assert capabilities.status_code == 200
                accepted = await client.post("/v1/workflows/snapshot-http/execute", params={"project_id": "owned-project"}, json={
                    "inputs": {"question": "owned-input"}, "async": True, "execution_mode": "draft_test",
                })
                assert accepted.status_code == 200, accepted.status_code
                payload = accepted.json()
                assert payload["workflow_version"] == 2
                assert payload["execution_mode"] == "draft_test"
                assert "private-snapshot-value" not in accepted.text
                assert "definition_snapshot" not in payload
                assert "definition_digest" not in payload
                run_id = payload["id"]
                forbidden = await client.post("/v1/workflows/snapshot-http/execute", params={"project_id": "foreign-project"}, json={})
                assert forbidden.status_code == 404
                production = await client.post("/v1/workflows/snapshot-http/execute", params={"project_id": "owned-project"}, json={"execution_mode": "production"})
                assert production.status_code == 409
            # Change the draft after the queue accepts its immutable copy.
            async with sessions() as db:
                row = await db.get(Workflow, "snapshot-http")
                row.definition = {"nodes": [], "edges": []}
                row.version = 3
                await db.commit()
            await tasks.async_execute_workflow(run_id, "snapshot-http", {"question": "broker-input"}, "owned-project")
            async with sessions() as db:
                saved = (await db.execute(select(WorkflowExecution).where(WorkflowExecution.id == run_id))).scalar_one()
                assert saved.status == "completed"
                assert saved.output == {"result": "accepted owned-input"}
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(verify())
