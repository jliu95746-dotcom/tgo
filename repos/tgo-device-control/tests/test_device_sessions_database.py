"""Opt-in PostgreSQL tests using a fresh owned schema per test."""

import asyncio
import os
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import settings
from app.core.database import Base
from app.models.device import Device, DeviceSession
from app.schemas.device_session import SessionStart
from app.services.device_sessions import DeviceSessionService

pytestmark = pytest.mark.skipif(
    os.getenv("TGO_DEVICE_SESSION_DB_TEST") != "1",
    reason="requires explicitly enabled local PostgreSQL test",
)


@pytest_asyncio.fixture
async def database():
    schema = "device_session_test_" + uuid4().hex
    engine = create_async_engine(settings.database_url_async)
    assert engine.url.host in ("127.0.0.1", "localhost")
    assert schema.startswith("device_session_test_") and len(schema) == 52
    owned = engine.execution_options(schema_translate_map={None: schema})
    created = False
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
        created = True
        async with owned.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(owned, expire_on_commit=False)
        project, device = uuid4(), uuid4()
        async with sessions() as db:
            db.add(
                Device(
                    id=device,
                    project_id=project,
                    device_name="会话测试设备",
                    os="fixture",
                )
            )
            await db.commit()
        yield sessions, project, device
    finally:
        # Only this generated, already-created schema can be removed.
        if created:
            async with engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()


@pytest.mark.asyncio
async def test_full_run_records_steps_without_raw_arguments(database):
    sessions, project, device = database
    session, agent = uuid4(), uuid4()
    async with sessions() as db:
        service = DeviceSessionService(db)
        await service.start(
            project,
            device,
            session,
            SessionStart(agent_id=agent, agent_name="测试员工"),
        )
        first = await service.begin_step(project, device, session, "fs_read")
        await service.end_step(
            project, device, session, first, "completed", screenshots=1
        )
        detail = await service.get(project, session)
        assert (
            detail.status == "running"
        )  # A tool result is NOT the whole task result.
        assert detail.actions_count == 1 and detail.screenshots_count == 1
        second = await service.begin_step(project, device, session, "fs_write")
        await service.end_step(project, device, session, second, "failed")
        await service.finish(project, device, session, "failed")
        detail = await service.get(project, session)
        assert detail.status == "failed" and detail.failed_actions_count == 1
        assert detail.ended_at is not None and len(detail.steps) == 2
        assert all(
            "arguments" not in step.model_dump()
            and "result" not in step.model_dump()
            for step in detail.steps
        )
        with pytest.raises(HTTPException) as error:
            await service.get(uuid4(), session)
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_expiry_cannot_be_revived_and_late_result_cannot_override_cancel(
    database,
):
    sessions, project, device = database
    session = uuid4()
    async with sessions() as db:
        service = DeviceSessionService(db)
        await service.start(
            project,
            device,
            session,
            SessionStart(agent_id=uuid4(), agent_name="测试"),
        )
        step = await service.begin_step(project, device, session, "fixture")
        await service.finish(project, device, session, "cancelled")
        await service.end_step(project, device, session, step, "completed")
        await service.finish(project, device, session, "completed")
        detail = await service.get(project, session)
        assert (
            detail.status == "cancelled"
            and detail.steps[0].status == "interrupted"
        )
        expired = uuid4()
        await service.start(
            project,
            device,
            expired,
            SessionStart(agent_id=uuid4(), agent_name="测试"),
        )
        row = await db.get(DeviceSession, expired)
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
        await db.commit()
        assert (await service.get(project, expired)).status == "interrupted"
        for action in (
            service.heartbeat(project, device, expired),
            service.begin_step(project, device, expired, "fixture"),
            service.finish(project, device, expired, "completed"),
        ):
            with pytest.raises(HTTPException) as error:
                await action
            assert error.value.status_code == 409
            await db.rollback()


@pytest.mark.asyncio
async def test_parallel_steps_and_duplicate_start_preserve_counts(database):
    sessions, project, device = database
    session, agent = uuid4(), uuid4()
    request = SessionStart(agent_id=agent, agent_name="并发测试")

    async def start():
        async with sessions() as db:
            await DeviceSessionService(db).start(
                project, device, session, request
            )

    await asyncio.gather(start(), start())

    async def step():
        async with sessions() as db:
            service = DeviceSessionService(db)
            step_id = await service.begin_step(
                project, device, session, "fixture"
            )
            await service.end_step(
                project, device, session, step_id, "completed"
            )

    await asyncio.gather(*(step() for _ in range(5)))
    async with sessions() as db:
        service = DeviceSessionService(db)
        await service.finish(project, device, session, "completed")
        detail = await service.get(project, session)
        assert detail.actions_count == 5 and len(detail.steps) == 5
        page = await service.list(project, limit=1)
        assert page.total == 1 and page.sessions[0].id == session
        assert (await service.list(uuid4())).total == 0
        assert len((await db.scalars(select(DeviceSession))).all()) == 1


@pytest.mark.asyncio
async def test_migration_preserves_legacy_rows_without_inventing_success(
    database,
):
    sessions, project, device = database
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0005_device_session_monitor.py"
    )
    spec = importlib.util.spec_from_file_location(
        "owned_session_migration", path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    legacy = uuid4()
    async with sessions() as db:
        connection = await db.connection()
        schema = connection.sync_connection.get_execution_options()[
            "schema_translate_map"
        ][None]
        assert schema.startswith("device_session_test_") and len(schema) == 52
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

        def apply(sync_connection, function):
            with Operations.context(
                MigrationContext.configure(sync_connection)
            ):
                function()

        await connection.run_sync(apply, migration.downgrade)
        await connection.execute(
            text(
                f'INSERT INTO "{schema}".dc_sessions '
                "(id, device_id, started_at, "
                "screenshots_count, actions_count) "
                "VALUES (:id, :device, CURRENT_TIMESTAMP, 3, 7)"
            ),
            {"id": legacy, "device": device},
        )
        await connection.run_sync(apply, migration.upgrade)
        await db.commit()
        detail = await DeviceSessionService(db).get(project, legacy)
        assert detail.actions_count == 7 and detail.screenshots_count == 3
        assert detail.status == "interrupted" and detail.ended_at is None
        assert detail.steps == [] and detail.step_total == 0
