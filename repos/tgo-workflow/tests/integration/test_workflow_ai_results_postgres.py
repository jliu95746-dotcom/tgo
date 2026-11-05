"""Independent PostgreSQL transactions prove claim and cancellation ordering."""
import asyncio
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.models.execution import WorkflowExecution
from app.models.workflow import Workflow
from app.schemas.ai_usage import WorkflowAICacheCommand, WorkflowAICacheWrite
from app.services import workflow_ai_results as results
from app.services.execution_snapshot import definition_digest

TEST_URL = os.getenv("WORKFLOW_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL,
    reason="Disposable PostgreSQL database required",
)


@pytest_asyncio.fixture
async def database():
    if (
        not TEST_URL
        or make_url(TEST_URL).get_backend_name() != "postgresql"
        or not (make_url(TEST_URL).database or "").startswith(
            "tgo_scenarios_test_"
        )
    ):
        pytest.fail("Disposable PostgreSQL database required", pytrace=False)
    schema = "ai_results_" + uuid4().hex
    admin = create_async_engine(TEST_URL)
    scoped = create_async_engine(
        TEST_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    created = False
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        yield scoped
    finally:
        await scoped.dispose()
        if created:
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest_asyncio.fixture
async def case(database, monkeypatch):
    async with database.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(database, expire_on_commit=False)
    project, workflow, execution, agent = [str(uuid4()) for _ in range(4)]
    graph = {
        "nodes": [
            {
                "id": "agent",
                "type": "agent",
                "data": {
                    "agent_id": agent,
                    "reference_key": "reply",
                },
            }
        ],
        "edges": [],
    }
    async with factory() as db:
        db.add(
            Workflow(
                id=workflow,
                project_id=project,
                name="Synthetic",
                definition=graph,
            )
        )
        await db.flush()
        db.add(
            WorkflowExecution(
                id=execution,
                project_id=project,
                workflow_id=workflow,
                status="running",
                workflow_version=1,
                execution_mode="draft_test",
                definition_snapshot=graph,
                definition_digest=definition_digest(graph),
                ai_usage_owner={
                    "staff_id": str(uuid4()),
                    "staff_token_version": 1,
                },
            )
        )
        await db.commit()
    monkeypatch.setattr(
        settings,
        "SECRET_KEY",
        SecretStr("synthetic-ai-result-postgres-secret-20261105"),
    )
    monkeypatch.setattr(settings, "WORKFLOW_AI_RESULT_KEYS", None)
    monkeypatch.setattr(settings, "WORKFLOW_AI_RESULT_ACTIVE_KEY_ID", None)
    request = WorkflowAICacheCommand(
        command={
            "project_id": project,
            "execution_id": execution,
            "node_id": "agent",
            "payload": {
                "agent_id": agent,
                "message": "Synthetic private question",
            },
        },
        reservation_id=uuid4(),
        lease_id=uuid4(),
    )
    return factory, request


def write(request, claim_id, content="Synthetic private answer"):
    return WorkflowAICacheWrite(
        **request.model_dump(),
        claim_id=claim_id,
        result={
            "success": True,
            "content": content,
            "execution_id": uuid4(),
            "execution_reference": {
                "project_id": request.command.project_id,
                "agent_id": request.command.payload.agent_id,
                "channel": "internal",
                "configuration_fingerprint": "a" * 64,
                "policy_revision": "b" * 64,
            },
        },
    )


@pytest.mark.asyncio
async def test_concurrent_prepares_only_claim_one_original_call(
    case, monkeypatch
):
    factory, request = case
    barrier = asyncio.Barrier(2)
    original_read = results.read_context

    async def synchronized(*args):
        context = await original_read(*args)
        await barrier.wait()
        return context

    monkeypatch.setattr(results, "read_context", synchronized)

    async def claim():
        async with factory() as db:
            return await results.prepare(db, request)

    receipts = await asyncio.wait_for(asyncio.gather(claim(), claim()), 15)
    assert sorted(row.state for row in receipts) == ["claimed", "pending"]
    assert receipts[0].claim_id == receipts[1].claim_id


@pytest.mark.asyncio
async def test_concurrent_different_results_cannot_overwrite_one_another(case):
    factory, request = case
    async with factory() as db:
        receipt = await results.prepare(db, request)
    inputs = [
        write(request, receipt.claim_id, content)
        for content in ["first answer", "second answer"]
    ]

    async def save(value):
        async with factory() as db:
            try:
                return await results.store(db, value)
            except HTTPException as error:
                assert error.status_code == 409
                return None

    outputs = await asyncio.wait_for(
        asyncio.gather(*(save(value) for value in inputs)), 15
    )
    assert sum(row is not None for row in outputs) == 1
    winner = next(row for row in outputs if row is not None)
    async with factory() as db:
        assert (await results.read(db, request)).result == winner.result


@pytest.mark.asyncio
async def test_cancellation_winning_row_lock_prevents_late_store(
    case, monkeypatch
):
    factory, request = case
    async with factory() as db:
        receipt = await results.prepare(db, request)
    ready = asyncio.Event()
    original_read = results.read_context

    async def observed(*args):
        context = await original_read(*args)
        ready.set()
        return context

    monkeypatch.setattr(results, "read_context", observed)
    async with factory() as locker:
        row = await locker.scalar(
            select(WorkflowExecution)
            .where(
                WorkflowExecution.id == str(request.command.execution_id),
            )
            .with_for_update()
        )

        async def late_store():
            async with factory() as db:
                with pytest.raises(HTTPException) as error:
                    await results.store(db, write(request, receipt.claim_id))
                assert error.value.status_code == 403

        pending = asyncio.create_task(late_store())
        try:
            await asyncio.wait_for(ready.wait(), 10)
            row.status = "cancelled"
            await locker.commit()
            await asyncio.wait_for(pending, 10)
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    async with factory() as db:
        row = await db.get(
            WorkflowExecution, str(request.command.execution_id)
        )
        assert row.status == "cancelled"
        assert "ciphertext" not in str(row.ai_result_receipts)


@pytest.mark.asyncio
async def test_migration_preserves_history_and_guards_receipt_removal(
    database,
):
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0024_workflow_ai_result_cache.py"
    )
    spec = importlib.util.spec_from_file_location("ai_result_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0023_workflow_ai_usage_owner"

    def verify(conn):
        conn.execute(
            text(
                "CREATE TABLE wf_workflow_executions (id VARCHAR PRIMARY KEY, output JSON)"
            )
        )
        conn.execute(
            text("INSERT INTO wf_workflow_executions VALUES ('legacy', '{}')")
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert conn.execute(
                text(
                    "SELECT id, ai_result_receipts, ai_result_expires_at FROM wf_workflow_executions"
                )
            ).one() == ("legacy", None, None)
            migration.downgrade()
            migration.upgrade()
            conn.execute(
                text(
                    "UPDATE wf_workflow_executions SET ai_result_receipts='{}'"
                )
            )
            with pytest.raises(RuntimeError, match="preserve"):
                migration.downgrade()
            assert (
                conn.scalar(
                    text("SELECT COUNT(*) FROM wf_workflow_executions")
                )
                == 1
            )

    async with database.begin() as conn:
        await conn.run_sync(verify)
