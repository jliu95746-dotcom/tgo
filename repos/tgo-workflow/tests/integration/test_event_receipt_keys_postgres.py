"""Real PostgreSQL migration and receipt races, isolated from business data."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.execution import WorkflowExecution
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.schemas.event_trigger import WorkflowEventRequest
from app.schemas.workflow_release import WorkflowPublishRequest
from app.services import event_trigger, workflow_releases

TEST_URL = os.getenv("WORKFLOW_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="Disposable PostgreSQL database required"
)
OLD = "old-receipt-material-for-tests-0123456789"
NEW = "new-receipt-material-for-tests-0123456789"


@pytest.fixture
async def database():
    if not TEST_URL or not (make_url(TEST_URL).database or "").startswith(
        "tgo_scenarios_test_"
    ):
        pytest.fail(
            "A disposable workflow test database is required", pytrace=False
        )
    schema = "receipt_keys_" + uuid4().hex
    admin = create_async_engine(TEST_URL)
    scoped = create_async_engine(
        TEST_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        yield scoped
    finally:
        await scoped.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(
                    text(f'DROP SCHEMA "{schema}" CASCADE')
                )
        await admin.dispose()


@pytest.mark.asyncio
async def test_postgres_migration_preserves_legacy_and_guards_modern_metadata(
    database,
):
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0017_event_receipt_keys.py"
    )
    spec = importlib.util.spec_from_file_location(
        "receipt_key_migration", path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def verify(connection):
        connection.execute(
            text(
                "CREATE TABLE wf_workflow_executions (id VARCHAR PRIMARY KEY, invocation_input_hmac VARCHAR(64))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO wf_workflow_executions VALUES ('legacy','original-proof')"
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(
                text(
                    "SELECT invocation_input_hmac,event_receipt_key_id FROM wf_workflow_executions"
                )
            ).one() == ("original-proof", None)
            connection.execute(
                text(
                    "INSERT INTO wf_workflow_executions VALUES ('modern','modern-proof','r1')"
                )
            )
            with pytest.raises(RuntimeError, match="版本化事件回执"):
                migration.downgrade()
            connection.execute(
                text("DELETE FROM wf_workflow_executions WHERE id='modern'")
            )
            migration.downgrade()
            assert (
                connection.execute(
                    text(
                        "SELECT invocation_input_hmac FROM wf_workflow_executions"
                    )
                ).scalar_one()
                == "original-proof"
            )

    async with database.begin() as connection:
        await connection.run_sync(verify)


@pytest.mark.asyncio
async def test_postgres_concurrent_event_then_rotation_never_duplicates(
    database, monkeypatch
):
    async with database.begin() as connection:
        for table in (
            Workflow.__table__,
            WorkflowRelease.__table__,
            WorkflowExecution.__table__,
        ):
            await connection.run_sync(table.create)
    sessions = async_sessionmaker(database, expire_on_commit=False)
    project_id, workflow_id = str(uuid4()), str(uuid4())
    graph = {
        "nodes": [
            {
                "id": "event",
                "type": "event",
                "data": {
                    "reference_key": "event",
                    "event_type": "test.receipt",
                },
            },
            {
                "id": "answer",
                "type": "answer",
                "data": {
                    "reference_key": "answer",
                    "output_type": "template",
                    "output_template": "received",
                },
            },
        ],
        "edges": [{"source": "event", "target": "answer"}],
    }
    monkeypatch.setattr(
        settings,
        "WORKFLOW_EVENT_RECEIPT_KEYS",
        SecretStr(json.dumps({"r1": OLD})),
    )
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID", "r1")
    monkeypatch.setattr(event_trigger, "admit_event", AsyncMock())
    enqueue = AsyncMock()
    monkeypatch.setattr(event_trigger, "enqueue_pending_initial", enqueue)
    async with sessions() as db:
        db.add(
            Workflow(
                id=workflow_id,
                project_id=project_id,
                name="test",
                status="draft",
                definition=graph,
                version=1,
                tags=[],
            )
        )
        await db.commit()
        await workflow_releases.publish(
            db,
            workflow_id,
            project_id,
            WorkflowPublishRequest(
                expected_draft_version=1, expected_publication_revision=0
            ),
            str(uuid4()),
        )
        await db.commit()
        context = await event_trigger.event_test_context(
            db, workflow_id, project_id
        )
    request = WorkflowEventRequest(
        project_id=project_id,
        event_type="test.receipt",
        expected_release_id=context.release_id,
        expected_release_digest=context.release_digest,
        data={"text": "synthetic fragment"},
    )
    barrier = asyncio.Barrier(2)
    original = event_trigger._saved_event

    async def synchronized_read(db, execution_id, project):
        row = await original(db, execution_id, project)
        if row is None:
            await barrier.wait()
        return row

    monkeypatch.setattr(event_trigger, "_saved_event", synchronized_read)

    async def submit():
        async with sessions() as db:
            return await event_trigger.accept_event(
                db, workflow_id, request, "concurrent", OLD
            )

    results = await asyncio.wait_for(
        asyncio.gather(submit(), submit()), timeout=20
    )
    assert sorted(item.deduplicated for item in results) == [False, True]
    assert results[0].execution_id == results[1].execution_id
    enqueue.assert_awaited_once()
    monkeypatch.setattr(event_trigger, "_saved_event", original)
    monkeypatch.setattr(
        settings,
        "WORKFLOW_EVENT_RECEIPT_KEYS",
        SecretStr(json.dumps({"r1": OLD, "r2": NEW})),
    )
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID", "r2")
    async with sessions() as db:
        run = await db.get(WorkflowExecution, results[0].execution_id)
        run.status, run.input = "completed", None
        await db.commit()
        duplicate = await event_trigger.accept_event(
            db, workflow_id, request, "concurrent", NEW
        )
        assert duplicate.deduplicated and duplicate.execution_id == run.id
        with pytest.raises(HTTPException) as conflict:
            await event_trigger.accept_event(
                db,
                workflow_id,
                request.model_copy(update={"data": {"changed": True}}),
                "concurrent",
                NEW,
            )
        assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_upgrade_from_development_revision_uses_given_connection_and_preserves_pending(
    database, monkeypatch
):
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    # Ignoring the explicitly supplied connection must fail against this
    # unused address, never migrate a configured business database.
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql+asyncpg://synthetic:unused@127.0.0.1:1/never-used",
    )

    def upgrade(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, "0011_aftersales_intake")
        connection.execute(
            text(
                "INSERT INTO wf_workflows (id,project_id,name,definition,status,version,tags) VALUES ('preserved','project','draft','{}','draft',1,'{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO wf_workflow_executions (id,project_id,workflow_id,status,input) VALUES ('pending','project','preserved','pending',cast(:input as json))"
            ),
            {"input": '{"text":"synthetic fixture"}'},
        )
        command.upgrade(config, "head")
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "0017_event_receipt_keys"
        )
        assert (
            connection.execute(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='alembic_version' AND column_name='version_num'"
                )
            ).scalar_one()
            == 128
        )
        assert (
            connection.execute(
                text(
                    "SELECT input->>'text' FROM wf_workflow_executions WHERE id='pending'"
                )
            ).scalar_one()
            == "synthetic fixture"
        )
        assert (
            connection.execute(
                text(
                    "SELECT event_receipt_key_id FROM wf_workflow_executions WHERE id='pending'"
                )
            ).scalar_one()
            is None
        )

    async with database.begin() as connection:
        await connection.run_sync(upgrade)
