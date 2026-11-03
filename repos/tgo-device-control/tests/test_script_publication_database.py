"""Real PostgreSQL migrations and races in disposable loopback schemas."""

import asyncio
import os
import re
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import settings
from app.models.device import Device
from app.models.device_scenario import DeviceScenarioDraft
from app.models.device_script_policy import DeviceScriptPolicy
from app.schemas.device_read_policy import DeviceReadPolicyPublish
from app.schemas.device_script_publication import ScriptPolicyRevoke
from app.services import device_script_publication as publication
from app.services.device_read_policy import DeviceReadPolicyService
from app.services.device_screen_publication import (
    DeviceScreenPublicationService,
)
from app.services.device_script_restore import restore_script_policy
from test_screen_publication import request as screen_request
from test_screen_publication_database import (
    independent_process_lock,
    migrate,
    migrations,
    selected_migrations,
)
from test_script_policy_contract import descriptor
from test_script_publication import connection, request, row

pytestmark = pytest.mark.skipif(
    os.getenv("TGO_SCRIPT_PUBLICATION_DB_TEST")
    != "owned-loopback-schema-only",
    reason="requires explicit owned loopback PostgreSQL schema opt-in",
)


@pytest_asyncio.fixture
async def script_database(monkeypatch):
    schema = "script_publication_test_" + uuid4().hex
    assert re.fullmatch(r"script_publication_test_[a-f0-9]{32}", schema)
    admin = create_async_engine(
        settings.database_url_async, hide_parameters=True
    )
    assert admin.url.host in {"127.0.0.1", "localhost", "::1"}
    engine = create_async_engine(
        settings.database_url_async,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    created = False
    project, device = uuid4(), uuid4()
    current = [connection(project, device)]
    close_calls = []

    def prepare(conn, nonce):
        active = False

        async def reply(method, params, timeout):
            nonlocal active
            if method == "scripts/status":
                return dict(
                    protocol_version=1,
                    state="active" if active else "awaiting_policy",
                    session_id=nonce,
                    reason="policy_confirmed" if active else "policy_required",
                )
            if method == "scripts/list":
                return dict(
                    protocol_version=1, scripts=[descriptor().model_dump()]
                )
            if method == "policy/apply":
                return {key: params[key] for key in ("revision", "digest")}
            assert method == "scripts/policy/apply"
            assert params["session_id"] == nonce
            active = True
            return dict(
                protocol_version=1,
                session_id=nonce,
                revision=params["policy"]["revision"],
                digest=params["policy"]["digest"],
            )

        conn.send_request.side_effect = reply
        return conn

    async def close(agent_id, expected_connection):
        close_calls.append(expected_connection)
        if current[0] is expected_connection:
            current[0] = None
        return True

    try:
        async with admin.begin() as db:
            await db.execute(CreateSchema(schema))
        created = True
        async with engine.begin() as db:
            assert await db.scalar(text("SELECT current_schema()")) == schema
            await db.run_sync(migrate, migrations(), "upgrade")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add(
                Device(
                    id=device,
                    project_id=project,
                    device_name="脚本策略隔离设备",
                    os="fixture",
                )
            )
            await db.commit()
        prepare(current[0], "d" * 32)
        monkeypatch.setattr(
            publication.tcp_connection_manager,
            "get_connection",
            lambda _: current[0],
        )
        monkeypatch.setattr(
            publication.tcp_connection_manager, "unregister_connection", close
        )
        yield sessions, project, device, current, prepare, close_calls, engine
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
async def test_two_process_style_first_publications_have_one_winner(
    script_database, monkeypatch
):
    sessions, project, device, _, _, _, _ = script_database
    monkeypatch.setattr(
        publication.tcp_connection_manager,
        "lifecycle_lock",
        independent_process_lock,
    )

    async def publish():
        async with sessions() as db:
            try:
                return await publication.DeviceScriptPublicationService(
                    db
                ).publish(project, device, request())
            except HTTPException as error:
                return error.status_code

    results = await asyncio.wait_for(asyncio.gather(publish(), publish()), 30)
    assert sum(result == 409 for result in results) == 1
    assert (
        sum(getattr(result, "status", None) == "active" for result in results)
        == 1
    )
    async with sessions() as db:
        saved = await db.get(DeviceScriptPolicy, device)
        assert saved.revision == 1 and saved.enabled
        assert await db.scalar(select(Device.id)) == device


@pytest.mark.asyncio
async def test_script_file_and_screen_modes_require_explicit_retirement(
    script_database,
):
    sessions, project, device, current, prepare, _, _ = script_database
    async with sessions() as db:
        db.add(
            DeviceScenarioDraft(
                device_id=device,
                project_id=project,
                scenario="files",
                purpose="隔离测试",
                target_directory="C:\\Business\\Fixture",
                revision=1,
            )
        )
        await db.commit()
        await publication.DeviceScriptPublicationService(db).publish(
            project, device, request()
        )
    for mode in ("files", "screen"):
        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                if mode == "files":
                    await DeviceReadPolicyService(db).publish(
                        project,
                        device,
                        DeviceReadPolicyPublish(
                            expected_draft_revision=1,
                            expected_policy_revision=0,
                        ),
                    )
                else:
                    await DeviceScreenPublicationService(db).publish(
                        project, device, screen_request()
                    )
            assert error.value.status_code == 409
    async with sessions() as db:
        await publication.DeviceScriptPublicationService(db).revoke(
            project, device, ScriptPolicyRevoke(expected_policy_revision=1)
        )
    current[0] = prepare(connection(project, device), "e" * 32)
    async with sessions() as db:
        result = await DeviceReadPolicyService(db).publish(
            project,
            device,
            DeviceReadPolicyPublish(
                expected_draft_revision=1, expected_policy_revision=0
            ),
        )
        assert result.status == "active" and result.verified is False
        history = await db.get(DeviceScriptPolicy, device)
        assert history.revision == 2 and not history.enabled
        assert current[0].script_policy_required is False


@pytest.mark.asyncio
async def test_reconnect_renegotiates_and_revocation_cannot_restore(
    script_database,
):
    sessions, project, device, current, prepare, closed, _ = script_database
    async with sessions() as db:
        await publication.DeviceScriptPublicationService(db).publish(
            project, device, request()
        )
    first = current[0]
    current[0] = prepare(connection(project, device), "e" * 32)
    async with sessions() as db:
        assert await restore_script_policy(db, current[0])
        result = await publication.DeviceScriptPublicationService(db).get(
            project, device
        )
        assert result.status == "active" and result.policy.revision == 1
        assert (
            first.applied_script_policy_ack.session_id
            != current[0].applied_script_policy_ack.session_id
        )
        await publication.DeviceScriptPublicationService(db).revoke(
            project, device, ScriptPolicyRevoke(expected_policy_revision=1)
        )
    assert len(closed) == 1
    current[0] = prepare(connection(project, device), "f" * 32)
    async with sessions() as db:
        assert await restore_script_policy(db, current[0])
        current[0].send_request.assert_not_awaited()
        assert current[0].script_policy_required is True
        result = await publication.DeviceScriptPublicationService(db).get(
            project, device
        )
        assert result.status == "revoked" and result.policy.revision == 2


@pytest.mark.asyncio
async def test_empty_migration_roundtrip_and_history_refuses_downgrade(
    script_database,
):
    sessions, project, device, _, _, _, engine = script_database
    target = selected_migrations("0013_device_script_policy")
    async with engine.begin() as db:
        await db.run_sync(migrate, target, "downgrade")
        assert not await db.run_sync(
            lambda conn: inspect(conn).has_table("dc_device_script_policies")
        )
        await db.run_sync(migrate, target, "upgrade")

        def assert_model(connection):
            actual = {
                column["name"]: column
                for column in inspect(connection).get_columns(
                    "dc_device_script_policies"
                )
            }
            expected = DeviceScriptPolicy.__table__.columns
            assert set(actual) == {column.name for column in expected}
            for column in expected:
                assert actual[column.name]["nullable"] == column.nullable
                assert actual[column.name]["type"].compile(
                    dialect=connection.dialect
                ) == column.type.compile(dialect=connection.dialect)
            constraints = {
                value["name"]
                for value in inspect(connection).get_check_constraints(
                    "dc_device_script_policies"
                )
            }
            assert {
                constraint.name
                for constraint in DeviceScriptPolicy.__table__.constraints
                if constraint.name
            } <= constraints

        await db.run_sync(assert_model)
    async with sessions() as db:
        assert await db.get(Device, device) is not None
        db.add(row(project, device))
        await db.commit()
    with pytest.raises(RuntimeError, match="Refusing to discard"):
        async with engine.begin() as db:
            await db.run_sync(migrate, target, "downgrade")
    async with sessions() as db:
        assert (await db.get(DeviceScriptPolicy, device)).revision == 1
