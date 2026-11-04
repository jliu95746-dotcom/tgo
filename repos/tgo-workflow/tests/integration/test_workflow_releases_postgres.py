"""Actual migration and publication races in a fresh PostgreSQL test schema."""

import asyncio
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services import workflow_releases as releases
from app.schemas.workflow_release import WorkflowPublishRequest

TEST_URL = os.getenv("WORKFLOW_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="An isolated PostgreSQL test database is required")


def load_migration(name):
    path = Path(__file__).parents[2] / "migrations" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_migration_freezes_legacy_and_publication_race_is_atomic():
    assert TEST_URL and (make_url(TEST_URL).database or "").startswith("tgo_scenarios_test_")
    schema = "release_test_" + uuid4().hex
    initial, snapshots, publication = [load_migration(name) for name in ["0001_init", "0002_execution_snapshots", "0003_workflow_releases"]]
    graph = '{"nodes": [{"id":"input","type":"input","data":{"reference_key":"input"}},{"id":"answer","type":"answer","data":{"reference_key":"answer","output_type":"template","output_template":"legacy"}}], "edges":[{"source":"input","target":"answer"}]}'

    def upgrade(connection):
        with Operations.context(MigrationContext.configure(connection)):
            initial.upgrade()
            snapshots.upgrade()
            connection.execute(text("INSERT INTO wf_workflows (id,project_id,name,definition,status,version,tags) VALUES ('legacy', 'project-a','历史流程',cast(:graph as json),'active',7,'{}')"), {"graph": graph})
            connection.execute(text("INSERT INTO wf_workflow_executions (id,project_id,workflow_id,status) VALUES ('legacy-run','project-a','legacy','completed')"))
            publication.upgrade()

    async def verify():
        admin = create_async_engine(TEST_URL)
        scoped = create_async_engine(TEST_URL, connect_args={"server_settings": {"search_path": schema}})
        schema_created = False
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            schema_created = True
            async with scoped.begin() as connection:
                await connection.run_sync(upgrade)
                assert (await connection.execute(text("SELECT published_version FROM wf_workflows WHERE id='legacy'"))).scalar_one() == 1
                assert (await connection.execute(text("SELECT definition->'nodes'->1->'data'->>'output_template' FROM wf_workflow_releases WHERE workflow_id='legacy'"))).scalar_one() == "legacy"
                assert (await connection.execute(text("SELECT release_id FROM wf_workflow_executions WHERE id='legacy-run'"))).scalar_one() is None
            sessions = async_sessionmaker(scoped, expire_on_commit=False)

            async def publish_once():
                async with sessions() as db:
                    try:
                        await releases.publish(db, "legacy", "project-a", WorkflowPublishRequest(expected_draft_version=7, expected_publication_revision=1), "staff-a")
                        await db.commit()
                        return "published"
                    except HTTPException as error:
                        await db.rollback()
                        assert error.status_code == 409
                        return "conflict"

            results = await asyncio.gather(publish_once(), publish_once())
            assert sorted(results) == ["conflict", "published"]
            async with scoped.begin() as connection:
                assert (await connection.execute(text("SELECT count(*) FROM wf_workflow_releases"))).scalar_one() == 2
                assert (await connection.execute(text("SELECT publication_revision FROM wf_workflows WHERE id='legacy'"))).scalar_one() == 2

                def downgrade(sync):
                    with Operations.context(MigrationContext.configure(sync)):
                        publication.downgrade()

                await connection.run_sync(downgrade)
                assert (await connection.execute(text("SELECT version FROM wf_workflows WHERE id='legacy'"))).scalar_one() == 7
                assert (await connection.execute(text("SELECT status FROM wf_workflow_executions WHERE id='legacy-run'"))).scalar_one() == "completed"
        finally:
            await scoped.dispose()
            # Remove only the fresh schema created above in the explicitly named test database.
            if schema_created:
                async with admin.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(verify())
