"""Opt-in full migration functions and durable races in an owned loopback schema."""

import asyncio
import importlib.util
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import settings
from app.core.database import get_async_db
from app.core.device_auth import require_service_principal
from app.main import app
from app.models.device import Device
from app.models.device_read_policy import DeviceReadPolicy
from app.models.device_scenario import DeviceScenarioDraft
from app.models.device_screen_policy import DeviceScreenPolicy
from app.schemas.device_access import DeviceServicePrincipal
from app.schemas.device_read_policy import (
    DeviceReadPolicyPublish,
    DeviceReadPolicyRevoke,
)
from app.schemas.device_screen_publication import ScreenPolicyRevoke
from app.services.device_read_policy import DeviceReadPolicyService
from app.services.device_screen_publication import DeviceScreenPublicationService
from app.services import device_screen_publication as publication
from test_screen_policy_contract import grant
from test_screen_publication import request

pytestmark = pytest.mark.skipif(
    os.getenv("TGO_SCREEN_PUBLICATION_DB_TEST") != "owned-loopback-schema-only",
    reason="requires explicit owned loopback PostgreSQL schema opt-in",
)


def migrations():
    modules = []
    for path in sorted((Path(__file__).parents[1] / "alembic/versions").glob("*.py")):
        if not re.fullmatch(r"\d{4}_[a-z_]+\.py", path.name):
            continue
        spec = importlib.util.spec_from_file_location(
            "screen_migration_" + path.stem, path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    assert len(modules) == 14 and modules[-1].revision == "0014_device_script_executions"
    assert all(
        current.down_revision == previous.revision
        for previous, current in zip(modules, modules[1:])
    )
    return modules


def selected_migrations(revision):
    selected = [module for module in migrations() if module.revision == revision]
    assert len(selected) == 1
    return selected


def migrate(connection, modules, direction):
    with Operations.context(MigrationContext.configure(connection)):
        for module in modules:
            getattr(module, direction)()


@asynccontextmanager
async def independent_process_lock(_):
    """Remove only in-process serialization when testing the real database lock."""
    yield


@pytest_asyncio.fixture
async def database(monkeypatch):
    schema = "screen_publication_test_" + uuid4().hex
    assert re.fullmatch(r"screen_publication_test_[a-f0-9]{32}", schema)
    admin = create_async_engine(settings.database_url_async, hide_parameters=True)
    assert admin.url.host in {"127.0.0.1", "localhost", "::1"}
    engine = create_async_engine(
        settings.database_url_async,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    created = False
    try:
        async with admin.begin() as db:
            await db.execute(CreateSchema(schema))
        created = True
        async with engine.begin() as db:
            assert await db.scalar(text("SELECT current_schema()")) == schema
            await db.run_sync(migrate, migrations(), "upgrade")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        project, device = uuid4(), uuid4()
        async with sessions() as db:
            db.add(
                Device(
                    id=device, project_id=project, device_name="屏幕策略合成设备", os="fixture"
                )
            )
            await db.commit()

        async def reply(method, params, timeout):
            if method == "screen/status":
                return grant().model_dump()
            if method == "policy/apply":
                return {key: params[key] for key in ("revision", "digest")}
            assert method == "screen/policy/apply"
            return {
                key: params[key]
                for key in ("protocol_version", "revision", "grant_id", "digest")
            }

        conn = SimpleNamespace(
            agent_id=str(device),
            project_id=str(project),
            connection_instance_id="c" * 32,
            desired_read_policy_digest=None,
            desired_screen_policy=None,
            applied_screen_policy_digest=None,
            screen_grant=None,
            send_request=AsyncMock(side_effect=reply),
        )
        monkeypatch.setattr(
            publication.tcp_connection_manager, "get_connection", lambda _: conn
        )
        close = AsyncMock()
        monkeypatch.setattr(
            publication.tcp_connection_manager, "unregister_connection", close
        )
        yield sessions, project, device, conn, close, engine
    finally:
        await engine.dispose()
        if created:
            async with admin.begin() as db:
                await db.execute(DropSchema(schema, cascade=True))
                assert not await db.scalar(
                    text("SELECT 1 FROM pg_namespace WHERE nspname=:name"),
                    {"name": schema},
                )
        await admin.dispose()


@pytest.mark.asyncio
async def test_concurrent_initial_publish_has_one_winner_and_survives_new_session(
    database,
):
    sessions, project, device, conn, _, _ = database

    async def publish():
        async with sessions() as db:
            try:
                return await DeviceScreenPublicationService(db).publish(
                    project, device, request()
                )
            except HTTPException as error:
                return error.status_code

    # Separate lifecycle managers are modeled by bypassing only the process lock;
    # the real PostgreSQL device-row lock must still serialize first publication.
    with patch.object(
        publication.tcp_connection_manager, "lifecycle_lock", independent_process_lock
    ):
        results = await asyncio.gather(publish(), publish())
    assert sum(result == 409 for result in results) == 1
    winner = next(result for result in results if result != 409)
    assert winner.policy.revision == 1 and not winner.verified
    async with sessions() as db:
        stored = await db.get(DeviceScreenPolicy, device)
        assert stored.digest == winner.policy.digest
        assert stored.enabled and stored.project_id == project
    # A fresh HTTP/TCP process has no acknowledgement. Durable desired scope
    # must not restore a window grant merely because its row survived.
    conn.desired_screen_policy = None
    conn.applied_screen_policy_digest = None
    async with sessions() as db:
        current = await DeviceScreenPublicationService(db).get(project, device)
        assert current.status == "pending" and current.verified is False


@pytest.mark.asyncio
async def test_revoke_races_with_update_without_losing_tombstone(database):
    sessions, project, device, conn, close, _ = database
    async with sessions() as db:
        await DeviceScreenPublicationService(db).publish(project, device, request())

    async def run(revoke):
        async with sessions() as db:
            try:
                service = DeviceScreenPublicationService(db)
                if revoke:
                    return await service.revoke(
                        project, device, ScreenPolicyRevoke(expected_policy_revision=1)
                    )
                return await service.publish(
                    project, device, request(expected_policy_revision=1)
                )
            except HTTPException as error:
                return error.status_code

    with patch.object(
        publication.tcp_connection_manager, "lifecycle_lock", independent_process_lock
    ):
        results = await asyncio.gather(run(True), run(False))
    assert sum(result == 409 for result in results) == 1
    async with sessions() as db:
        stored = await db.get(DeviceScreenPolicy, device)
        assert stored.revision == 2
        if stored.enabled:
            await DeviceScreenPublicationService(db).revoke(
                project, device, ScreenPolicyRevoke(expected_policy_revision=2)
            )
    async with sessions() as db:
        stored = await db.get(DeviceScreenPolicy, device)
        assert stored.enabled is False and stored.revoked_at is not None
        assert stored.revision in {2, 3}
    assert conn.applied_screen_policy_digest is None and close.await_count >= 1


@pytest.mark.asyncio
async def test_file_and_screen_policies_cannot_both_be_published(database):
    sessions, project, device, _, _, _ = database
    async with sessions() as db:
        await DeviceScreenPublicationService(db).publish(project, device, request())
        db.add(
            DeviceScenarioDraft(
                device_id=device,
                project_id=project,
                scenario="files",
                purpose="读取合成资料",
                target_directory="/srv/fixture",
                revision=1,
            )
        )
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await DeviceReadPolicyService(db).publish(
                project,
                device,
                DeviceReadPolicyPublish(
                    expected_draft_revision=1, expected_policy_revision=0
                ),
            )
        assert error.value.status_code == 409
        await db.rollback()
        assert await db.get(DeviceReadPolicy, device) is None


@pytest.mark.asyncio
async def test_concurrent_file_and_screen_first_publish_are_mutually_exclusive(
    database,
):
    sessions, project, device, _, _, _ = database
    async with sessions() as db:
        db.add(
            DeviceScenarioDraft(
                device_id=device,
                project_id=project,
                scenario="files",
                purpose="读取合成资料",
                target_directory="/srv/fixture",
                revision=1,
            )
        )
        await db.commit()

    async def publish(screen):
        async with sessions() as db:
            try:
                if screen:
                    return await DeviceScreenPublicationService(db).publish(
                        project, device, request()
                    )
                return await DeviceReadPolicyService(db).publish(
                    project,
                    device,
                    DeviceReadPolicyPublish(
                        expected_draft_revision=1, expected_policy_revision=0
                    ),
                )
            except HTTPException as error:
                return error.status_code

    with patch.object(
        publication.tcp_connection_manager, "lifecycle_lock", independent_process_lock
    ):
        results = await asyncio.gather(publish(True), publish(False))
    assert sum(result == 409 for result in results) == 1
    async with sessions() as db:
        screen = await db.get(DeviceScreenPolicy, device)
        file = await db.get(DeviceReadPolicy, device)
        assert (screen is None) != (file is None)


@pytest.mark.asyncio
async def test_http_ownership_publish_revoke_and_unsafe_downgrade(database):
    sessions, project, device, _, _, engine = database
    previous = app.dependency_overrides.copy()

    async def session():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_async_db] = session
    app.dependency_overrides[
        require_service_principal
    ] = lambda: DeviceServicePrincipal(sub="tgo-api", project_id=project)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.put(
                f"/v1/devices/{device}/screen-policy",
                json=request().model_dump(mode="json"),
            )
            assert result.status_code == 200 and result.json()["verified"] is False
            assert result.json()["status"] == "active"
            foreign = await client.get(f"/v1/devices/{uuid4()}/screen-policy")
            assert foreign.status_code == 404
            revoked = await client.post(
                f"/v1/devices/{device}/screen-policy/revoke",
                json={"expected_policy_revision": 1},
            )
            assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
            assert (await client.get(f"/v1/devices/{device}/screen-policy")).json()[
                "policy"
            ]["revision"] == 2
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    async with engine.begin() as db:
        with pytest.raises(RuntimeError, match="Refusing to discard"):
            await db.run_sync(migrate, selected_migrations("0011_device_screen_policy"), "downgrade")
    async with sessions() as db:
        assert (await db.get(DeviceScreenPolicy, device)).revision == 2


@pytest.mark.asyncio
async def test_empty_migration_can_downgrade_upgrade_and_matches_model(database):
    _, _, _, _, _, engine = database
    async with engine.begin() as db:
        await db.run_sync(migrate, selected_migrations("0011_device_screen_policy"), "downgrade")
        await db.run_sync(migrate, selected_migrations("0011_device_screen_policy"), "upgrade")
        columns = set(
            (
                await db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=current_schema() "
                        "AND table_name='dc_device_screen_policies'"
                    )
                )
            ).scalars()
        )
        assert columns == set(DeviceScreenPolicy.__table__.columns.keys())


@pytest.mark.asyncio
async def test_explicit_switch_retains_both_versions_and_requires_fresh_local_grant(
    database,
):
    sessions, project, device, conn, _, _ = database
    async with sessions() as db:
        db.add(
            DeviceScenarioDraft(
                device_id=device,
                project_id=project,
                scenario="files",
                purpose="读取合成资料",
                target_directory="/srv/fixture",
                revision=1,
            )
        )
        await db.commit()
        files = DeviceReadPolicyService(db)
        published = await files.publish(
            project,
            device,
            DeviceReadPolicyPublish(
                expected_draft_revision=1, expected_policy_revision=0
            ),
        )
        assert published.revision == 1
        retired = await files.revoke(
            project, device, DeviceReadPolicyRevoke(expected_policy_revision=1)
        )
        assert (
            retired.status == "revoked"
            and retired.revision == 2
            and not retired.verified
        )

    # Model a newly authorized local instance after the old transport was closed.
    # This suite's TCP replies remain synthetic; the database and HTTP are real.
    conn.connection_instance_id = "d" * 32

    async def fresh_reply(method, params, timeout):
        if method == "screen/status":
            return grant().model_copy(update={"grant_id": "b" * 32}).model_dump()
        if method == "policy/apply":
            return {key: params[key] for key in ("revision", "digest")}
        return {
            key: params[key]
            for key in ("protocol_version", "revision", "grant_id", "digest")
        }

    conn.send_request.side_effect = fresh_reply
    async with sessions() as db:
        service = DeviceScreenPublicationService(db)
        with pytest.raises(HTTPException) as error:
            await service.publish(
                project, device, request(connection_instance_id="d" * 32)
            )
        assert error.value.status_code == 422
        await db.rollback()
        screen = await service.publish(
            project, device, request(connection_instance_id="d" * 32, grant_id="b" * 32)
        )
        assert screen.status == "active" and not screen.verified
        assert (await db.get(DeviceReadPolicy, device)).enabled is False
        await service.revoke(
            project, device, ScreenPolicyRevoke(expected_policy_revision=1)
        )
    async with sessions() as db:
        files = await DeviceReadPolicyService(db).publish(
            project,
            device,
            DeviceReadPolicyPublish(
                expected_draft_revision=1, expected_policy_revision=2
            ),
        )
        assert files.revision == 3 and not files.verified
        assert (await db.get(DeviceScreenPolicy, device)).enabled is False


@pytest.mark.asyncio
async def test_read_revocation_migration_preserves_old_active_rows_and_rejects_history_loss(
    database,
):
    sessions, project, device, conn, _, engine = database
    async with sessions() as db:
        db.add(
            DeviceReadPolicy(
                device_id=device,
                project_id=project,
                revision=1,
                enabled=True,
                allowed_directory="/srv/fixture",
                max_lines=10,
                max_offset=10,
                digest="f" * 64,
            )
        )
        await db.commit()
    async with engine.begin() as db:
        await db.run_sync(migrate, selected_migrations("0012_device_read_revocation"), "downgrade")
        await db.run_sync(migrate, selected_migrations("0012_device_read_revocation"), "upgrade")
    async with sessions() as db:
        policy = await db.get(DeviceReadPolicy, device)
        assert policy.enabled is True and policy.revoked_at is None
        conn.desired_read_policy_digest = policy.digest
        result = await DeviceReadPolicyService(db).revoke(
            project, device, DeviceReadPolicyRevoke(expected_policy_revision=1)
        )
        assert result.status == "revoked"
    async with engine.begin() as db:
        with pytest.raises(RuntimeError, match="Refusing to discard"):
            await db.run_sync(migrate, selected_migrations("0012_device_read_revocation"), "downgrade")
