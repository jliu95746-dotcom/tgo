"""Migrations and concurrent schedule commands on disposable PostgreSQL."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_schedule import (
    WorkflowSchedule,
    WorkflowScheduleCommand,
)
from app.schemas.workflow_schedule import ScheduleDisable, ScheduleSave
from app.services import workflow_schedules as schedules
from app.services.execution_snapshot import copy_json_object, definition_digest

TEST_URL = os.getenv("WORKFLOW_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="Disposable PostgreSQL database required"
)


@pytest_asyncio.fixture
async def database(monkeypatch: pytest.MonkeyPatch):
    from app.services import node_readiness

    monkeypatch.setattr(
        node_readiness,
        "SUPPORTED_NODE_TYPES",
        node_readiness.SUPPORTED_NODE_TYPES | {"timer"},
    )
    assert TEST_URL is not None
    url = make_url(TEST_URL)
    if url.host not in {"127.0.0.1", "localhost", "::1"} or not (
        url.database or ""
    ).startswith("tgo_scenarios_test_"):
        pytest.fail(
            "Only an explicitly disposable loopback database is permitted",
            pytrace=False,
        )
    schema = "schedule_" + uuid4().hex
    admin = create_async_engine(TEST_URL, hide_parameters=True)
    engine = create_async_engine(
        TEST_URL,
        connect_args={"server_settings": {"search_path": schema}},
        hide_parameters=True,
    )
    async with admin.begin() as db:
        await db.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        # Use the supplied scoped connection, without rewriting any file.
        config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
        config.set_main_option(
            "script_location", str(Path(__file__).parents[2] / "migrations")
        )
        monkeypatch.setattr(settings, "DATABASE_URL", TEST_URL)
        async with engine.connect() as connection:
            await connection.run_sync(lambda sync: _upgrade(config, sync))
        yield engine, schema
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def _upgrade(config, connection) -> None:
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


def migration_head() -> str | None:
    root = Path(__file__).parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


async def seed(engine):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, actor = uuid4(), uuid4()
    workflow_id, release_id = str(uuid4()), str(uuid4())
    graph = copy_json_object(
        {
            "nodes": [
                {
                    "id": "clock",
                    "type": "timer",
                    "data": {
                        "reference_key": "clock",
                        "label": "合成定时",
                        "type": "timer",
                        "cron_expression": "0 9 * * *",
                        "timezone": "Asia/Shanghai",
                    },
                },
                {
                    "id": "answer",
                    "type": "answer",
                    "data": {
                        "reference_key": "answer",
                        "output_type": "template",
                        "output_template": "模拟结果",
                    },
                },
            ],
            "edges": [{"source": "clock", "target": "answer"}],
        }
    )
    digest = definition_digest(graph)
    async with sessions() as db:
        db.add(
            Workflow(
                id=workflow_id,
                project_id=str(project),
                name="合成计划",
                definition=graph,
                status="active",
                version=1,
                published_version=1,
                publication_revision=1,
            )
        )
        await db.flush()
        db.add(
            WorkflowRelease(
                id=release_id,
                workflow_id=workflow_id,
                project_id=str(project),
                version=1,
                draft_version=1,
                definition=graph,
                definition_digest=digest,
            )
        )
        await db.commit()
    request = ScheduleSave(
        request_id=uuid4(),
        expected_revision=0,
        expected_release_id=release_id,
        expected_release_digest=digest,
        enabled=False,
    )
    return sessions, project, actor, workflow_id, request


@pytest.mark.asyncio
async def test_same_command_concurrently_commits_one_plan_and_reply(
    database,
) -> None:
    engine, _ = database
    sessions, project, actor, flow, request = await seed(engine)

    async def save():
        async with sessions() as db:
            return await schedules.save_schedule(
                db, flow, project, actor, request
            )

    results = await asyncio.gather(*(save() for _ in range(5)))
    assert sum(not result.replayed for result in results) == 1
    assert len({result.schedule.id for result in results}) == 1
    async with sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowSchedule))
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowScheduleCommand)
            )
            == 1
        )
        assert (
            await db.scalar(text("SELECT version_num FROM alembic_version"))
            == migration_head()
        )


@pytest.mark.asyncio
async def test_distinct_stale_commands_have_one_winner(database) -> None:
    engine, _ = database
    sessions, project, actor, flow, original = await seed(engine)

    async def save():
        async with sessions() as db:
            try:
                return await schedules.save_schedule(
                    db,
                    flow,
                    project,
                    actor,
                    original.model_copy(update={"request_id": uuid4()}),
                )
            except HTTPException as error:
                return error.status_code

    results = await asyncio.gather(save(), save())
    assert sum(result == 409 for result in results) == 1
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowScheduleCommand)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_original_receipt_recovers_in_another_process_after_stop(
    database,
) -> None:
    engine, schema = database
    sessions, project, actor, flow, request = await seed(engine)
    async with sessions() as db:
        first = await schedules.save_schedule(
            db, flow, project, actor, request
        )
        await schedules.disable_schedule(
            db,
            flow,
            project,
            actor,
            ScheduleDisable(request_id=uuid4(), expected_revision=1),
        )
        await db.execute(
            update(Workflow).where(Workflow.id == flow).values(status="draft")
        )
        await db.commit()
    expected = hashlib.sha256(
        first.schedule.model_dump_json().encode()
    ).hexdigest()
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_URL
    env["SCHEDULE_TEST_SCHEMA"] = schema
    env["SCHEDULE_TEST_RECOVERY"] = json.dumps(
        [flow, str(project), str(actor), str(request.request_id), expected]
    )
    source = """
import asyncio, hashlib, json, logging, os
from uuid import UUID
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
logging.disable(logging.CRITICAL)
from app.services.workflow_schedules import get_command_receipt
async def verify():
    values = json.loads(os.environ["SCHEDULE_TEST_RECOVERY"])
    flow, project, actor, request, digest = values
    engine = create_async_engine(
        os.environ["DATABASE_URL"], hide_parameters=True,
        connect_args={"server_settings": {
            "search_path": os.environ["SCHEDULE_TEST_SCHEMA"]}},
    )
    try:
        async with async_sessionmaker(engine)() as db:
            result = await get_command_receipt(
                db, flow, UUID(project), UUID(actor), UUID(request))
            assert result.replayed and result.schedule.revision == 1
            actual = result.schedule.model_dump_json().encode()
            assert hashlib.sha256(actual).hexdigest() == digest
    finally:
        await engine.dispose()
asyncio.run(verify())
print("RECOVERED")
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", source],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=Path(__file__).parents[2],
    )
    assert result.returncode == 0, "Original receipt recovery process failed"
    assert result.stdout.strip() == "RECOVERED"


@pytest.mark.asyncio
async def test_nonempty_downgrade_retains_timer_rows(database) -> None:
    engine, _ = database
    sessions, project, actor, flow, request = await seed(engine)
    async with sessions() as db:
        await schedules.save_schedule(db, flow, project, actor, request)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    async with engine.connect() as connection:

        def downgrade(sync):
            config.attributes["connection"] = sync
            with pytest.raises(RuntimeError, match="请求回执"):
                command.downgrade(config, "0017_event_receipt_keys")

        await connection.run_sync(downgrade)
        await connection.rollback()
    async with sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowSchedule))
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowScheduleCommand)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_request_collision_across_workflows_rolls_back_the_losing_plan(
    database,
) -> None:
    engine, _ = database
    sessions, project, actor, flow, original = await seed(engine)
    second_flow, second_release = str(uuid4()), str(uuid4())
    async with sessions() as db:
        source = await db.get(WorkflowRelease, original.expected_release_id)
        assert source is not None
        db.add(
            Workflow(
                id=second_flow,
                project_id=str(project),
                name="第二个合成流程",
                definition=source.definition,
                status="active",
                version=1,
                published_version=1,
                publication_revision=1,
            )
        )
        await db.flush()
        db.add(
            WorkflowRelease(
                id=second_release,
                workflow_id=second_flow,
                project_id=str(project),
                version=1,
                draft_version=1,
                definition=source.definition,
                definition_digest=source.definition_digest,
            )
        )
        await db.commit()

    async def save(workflow_id, request):
        async with sessions() as db:
            try:
                return await schedules.save_schedule(
                    db, workflow_id, project, actor, request
                )
            except HTTPException as error:
                return error.status_code

    second = original.model_copy(
        update={"expected_release_id": second_release}
    )
    results = await asyncio.gather(
        save(flow, original), save(second_flow, second)
    )
    assert sum(result == 409 for result in results) == 1
    async with sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowSchedule))
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowScheduleCommand)
            )
            == 1
        )


async def seed_due(engine):
    from datetime import datetime, timezone

    sessions, project, actor, flow, request = await seed(engine)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    async with sessions() as db:
        result = await schedules.save_schedule(
            db, flow, project, actor, request
        )
        await db.execute(
            update(WorkflowSchedule).values(enabled=True, next_run_at=now)
        )
        await db.commit()
    return sessions, project, flow, str(result.schedule.id), now


@pytest.mark.asyncio
async def test_concurrent_timer_collectors_and_dispatchers_create_one_run(
    database, monkeypatch
):
    from unittest.mock import AsyncMock
    from app.services import timer_dispatch as dispatch
    from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
    from app.models.execution import WorkflowExecution

    engine, _ = database
    sessions, project, flow, plan, now = await seed_due(engine)

    async def collect():
        async with sessions() as db:
            return await dispatch.materialize_due(db, plan, now=now)

    collected = await asyncio.gather(*(collect() for _ in range(5)))
    identifiers = [value for value in collected if value is not None]
    assert len(identifiers) == 1
    identifier = identifiers[0]
    monkeypatch.setattr(dispatch, "verify_timer_authority", AsyncMock())
    enqueue = AsyncMock()
    monkeypatch.setattr(dispatch, "enqueue_pending_initial", enqueue)

    async def deliver():
        async with sessions() as db:
            return await dispatch.dispatch_occurrence(db, identifier, now=now)

    delivered = await asyncio.gather(*(deliver() for _ in range(5)))
    assert len([value for value in delivered if value is not None]) == 1
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowTimerOccurrence)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowExecution)
            )
            == 1
        )
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_process_recovers_due_fact_and_runs_engine_once(database):
    from app.services.timer_dispatch import materialize_due
    from app.models.execution import WorkflowExecution, NodeExecution

    engine, schema = database
    sessions, project, flow, plan, now = await seed_due(engine)
    async with sessions() as db:
        identifier = await materialize_due(db, plan, now=now)
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_URL
    env["TIMER_TEST_RESTART"] = json.dumps(
        [schema, identifier, flow, str(project)]
    )
    source = """
import asyncio, json, logging, os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
logging.disable(logging.CRITICAL)
from app.services import timer_dispatch, timer_runtime
from app.services import node_readiness
from celery_app import tasks
node_readiness.SUPPORTED_NODE_TYPES |= {"timer"}
async def verify():
    values = json.loads(os.environ["TIMER_TEST_RESTART"])
    schema, identifier, flow, project = values
    engine = create_async_engine(os.environ["DATABASE_URL"],
        connect_args={"server_settings":{"search_path":schema}},
        hide_parameters=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def session():
        async with sessions() as db:
            yield db
    tasks.workflow_task_session = session
    timer_dispatch.verify_timer_authority = AsyncMock()
    timer_dispatch.enqueue_pending_initial = AsyncMock()
    timer_runtime.verify_timer_authority = AsyncMock()
    try:
        async with sessions() as db:
            run = await timer_dispatch.dispatch_occurrence(db, identifier)
            assert run is not None
        await tasks._run_workflow(run, flow, {}, project)
        await tasks._run_workflow(run, flow, {}, project)
    finally:
        await engine.dispose()
asyncio.run(verify())
print("TIMER_RECOVERED")
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", source],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=Path(__file__).parents[2],
    )
    assert result.returncode == 0, "Timer recovery process failed"
    assert result.stdout.strip() == "TIMER_RECOVERED"
    async with sessions() as db:
        runs = list(await db.scalars(select(WorkflowExecution)))
        assert len(runs) == 1 and runs[0].status == "completed"
        assert runs[0].input is None
        assert (
            await db.scalar(select(func.count()).select_from(NodeExecution))
            == 2
        )


@pytest.mark.asyncio
async def test_timer_source_prevents_destructive_downgrade(database):
    from app.services.timer_dispatch import materialize_due
    from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence

    engine, _ = database
    sessions, project, flow, plan, now = await seed_due(engine)
    async with sessions() as db:
        await materialize_due(db, plan, now=now)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    async with engine.connect() as connection:
        starting_revision = await connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )

        def downgrade(sync):
            config.attributes["connection"] = sync
            with pytest.raises(RuntimeError, match="定时来源"):
                command.downgrade(config, "0018_workflow_schedules")

        await connection.run_sync(downgrade)
        await connection.rollback()
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowTimerOccurrence)
            )
            == 1
        )
        assert (
            await db.scalar(text("SELECT version_num FROM alembic_version"))
            == starting_revision
        )


@pytest.mark.asyncio
async def test_stop_waits_for_node_receipt_commit(database, monkeypatch):
    from unittest.mock import AsyncMock
    from uuid import UUID
    from app.services import timer_dispatch as dispatch, timer_runtime
    from app.services.execution_lifecycle import claim_execution
    from app.models.execution import WorkflowExecution, NodeExecution

    engine, _ = database
    sessions, project, flow, plan, now = await seed_due(engine)
    monkeypatch.setattr(dispatch, "verify_timer_authority", AsyncMock())
    monkeypatch.setattr(dispatch, "enqueue_pending_initial", AsyncMock())
    monkeypatch.setattr(timer_runtime, "verify_timer_authority", AsyncMock())
    async with sessions() as db:
        occurrence = await dispatch.materialize_due(db, plan, now=now)
        run = await dispatch.dispatch_occurrence(db, occurrence, now=now)
        assert await claim_execution(db, run, flow, str(project))
        actor = UUID((await db.get(WorkflowSchedule, plan)).updated_by)
        await timer_runtime.require_active_timer(
            db, str(project), flow, run, retain_lock=True
        )
        ready = asyncio.Event()
        backend = []

        async def stop():
            async with sessions() as other:
                backend.append(
                    await other.scalar(text("SELECT pg_backend_pid()"))
                )
                ready.set()
                return await schedules.disable_schedule(
                    other,
                    flow,
                    project,
                    actor,
                    ScheduleDisable(request_id=uuid4(), expected_revision=1),
                )

        pending = asyncio.create_task(stop())
        try:
            await asyncio.wait_for(ready.wait(), timeout=5)
            async with sessions() as observer:
                observed = False
                for _ in range(100):
                    state = await observer.scalar(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity "
                            "WHERE pid=:pid"
                        ),
                        {"pid": backend[0]},
                    )
                    if state == "Lock":
                        observed = True
                        break
                    assert not pending.done()
                    await observer.commit()
                    await asyncio.sleep(0.02)
                assert observed, "Stop must wait behind the node receipt lock"
            db.add(
                NodeExecution(
                    id=str(uuid4()),
                    project_id=str(project),
                    execution_id=run,
                    node_id="clock",
                    node_type="timer",
                    status="completed",
                    output={"timestamp": now.isoformat()},
                )
            )
            await db.commit()
        finally:
            await db.rollback()
            await asyncio.wait_for(pending, timeout=5)
    async with sessions() as db:
        assert (await db.get(WorkflowExecution, run)).status == "cancelled"
        assert (
            await db.scalar(select(func.count()).select_from(NodeExecution))
            == 1
        )


@pytest.mark.asyncio
async def test_upgrade_preserves_original_schedule_and_v1_reply(database):
    from datetime import datetime, timezone

    engine, _ = database
    root = Path(__file__).parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    async with engine.connect() as connection:

        def downgrade(sync):
            config.attributes["connection"] = sync
            command.downgrade(config, "0018_workflow_schedules")

        await connection.run_sync(downgrade)
    sessions, project, actor, flow, request = await seed(engine)
    identifier = str(uuid4())
    now = datetime.now(timezone.utc)
    legacy = {
        "id": identifier,
        "project_id": str(project),
        "workflow_id": flow,
        "revision": 1,
        "enabled": False,
        "release_id": request.expected_release_id,
        "release_digest": request.expected_release_digest,
        "cron_expression": "0 9 * * *",
        "timezone": "Asia/Shanghai",
        "next_run_at": None,
        "created_by": str(actor),
        "updated_by": str(actor),
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "updated_at": now.isoformat().replace("+00:00", "Z"),
    }
    original_request = {
        "request_id": str(request.request_id),
        "expected_revision": 0,
        "expected_release_id": request.expected_release_id,
        "expected_release_digest": request.expected_release_digest,
        "enabled": False,
    }
    proof = hashlib.sha256(
        json.dumps(
            {
                "contract": "workflow.schedule.command.v1",
                "workflow_id": flow,
                "project_id": str(project),
                "actor_id": str(actor),
                "operation": "save",
                "request": original_request,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    async with engine.begin() as db:
        await db.execute(
            text(
                "INSERT INTO wf_workflow_schedules "
                "(id,project_id,workflow_id,revision,enabled,release_id,"
                "release_digest,cron_expression,timezone,next_run_at,"
                "created_by,updated_by,created_at,updated_at) "
                "VALUES (:id,:project,:flow,1,FALSE,:release,:digest,"
                "'0 9 * * *',"
                "'Asia/Shanghai',NULL,:actor,:actor,:now,:now)"
            ),
            {
                "id": identifier,
                "project": str(project),
                "flow": flow,
                "release": request.expected_release_id,
                "digest": request.expected_release_digest,
                "actor": str(actor),
                "now": now,
            },
        )
        await db.execute(
            text(
                "INSERT INTO wf_workflow_schedule_commands "
                "(project_id,request_id,workflow_id,actor_staff_id,operation,"
                "request_digest,result_snapshot) VALUES "
                "(:project,:request,:flow,:actor,'save',:digest,:snapshot)"
            ),
            {
                "project": str(project),
                "request": str(request.request_id),
                "flow": flow,
                "actor": str(actor),
                "digest": proof,
                "snapshot": json.dumps(legacy),
            },
        )
    async with engine.connect() as connection:
        await connection.run_sync(lambda sync: _upgrade(config, sync))
    async with sessions() as db:
        result = await schedules.save_schedule(
            db, flow, project, actor, request
        )
        assert result.replayed is True
        assert result.schedule.model_dump(mode="json") == legacy
        assert (
            await db.get(WorkflowSchedule, identifier)
        ).suspension_reason is None
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowScheduleCommand)
            )
            == 1
        )
