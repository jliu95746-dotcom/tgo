"""Actual reservations serialize budgets and never persist business values."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import func, inspect, select, text

from app.models.device import DeviceSession, DeviceSessionStep
from app.models.device_script_execution import DeviceScriptExecution
from app.schemas.device_access import DeviceServicePrincipal
from app.services.device_script_execution_store import ScriptExecutionStore
from app.services.device_script_publication import (
    DeviceScriptPublicationService,
)
from test_script_policy_contract import descriptor
from test_script_publication import request
from test_screen_publication_database import migrate, selected_migrations
import test_script_publication_database as database_tests

pytestmark = database_tests.pytestmark
script_database = database_tests.script_database


@pytest.mark.asyncio
async def test_execution_migration_matches_model_and_preserves_history(
    script_database,
):
    sessions, project, device, _, _, _, engine = script_database
    target = selected_migrations("0014_device_script_executions")
    async with engine.begin() as db:
        await db.run_sync(migrate, target, "downgrade")
        assert not await db.run_sync(
            lambda conn: inspect(conn).has_table("dc_script_executions")
        )
        await db.run_sync(migrate, target, "upgrade")

        def assert_model(conn):
            inspector = inspect(conn)
            table = DeviceScriptExecution.__table__
            actual = {
                column["name"]: column
                for column in inspector.get_columns(table.name)
            }
            assert set(actual) == {column.name for column in table.columns}
            for column in table.columns:
                assert actual[column.name]["nullable"] == column.nullable
                assert actual[column.name]["type"].compile(
                    dialect=conn.dialect
                ) == column.type.compile(dialect=conn.dialect)
            assert set(
                inspector.get_pk_constraint(table.name)["constrained_columns"]
            ) == {column.name for column in table.primary_key}
            actual_checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(table.name)
            }
            expected_checks = {
                constraint.name
                for constraint in table.constraints
                if constraint.__class__.__name__ == "CheckConstraint"
            }
            assert actual_checks == expected_checks
            actual_unique = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints(table.name)
            }
            assert ("step_id",) in actual_unique
            assert {
                tuple(item["column_names"])
                for item in inspector.get_indexes(table.name)
                if not item.get("duplicates_constraint")
            } == {
                tuple(column.name for column in index.columns)
                for index in table.indexes
            }
            actual_fks = {
                (
                    tuple(item["constrained_columns"]),
                    item["referred_table"],
                    tuple(item["referred_columns"]),
                    item["options"].get("ondelete"),
                )
                for item in inspector.get_foreign_keys(table.name)
            }
            expected_fks = {
                (
                    (foreign.parent.name,),
                    foreign.column.table.name,
                    (foreign.column.name,),
                    foreign.ondelete,
                )
                for foreign in table.foreign_keys
            }
            assert actual_fks == expected_fks

        await db.run_sync(assert_model)
    principal = await prepare(sessions, project, device)
    execution = uuid4()
    async with sessions() as db:
        await ScriptExecutionStore(db).reserve(
            principal, device, execution, {}, descriptor()
        )
    with pytest.raises(
        RuntimeError,
        match="Refusing to discard script execution identity history",
    ):
        async with engine.begin() as db:
            await db.run_sync(migrate, target, "downgrade")
    async with sessions() as db:
        assert (
            await db.get(DeviceScriptExecution, (device, execution))
            is not None
        )
        assert (
            await db.get(DeviceSession, principal.session_id)
        ).actions_count == 1
        assert (
            await db.scalar(
                select(func.count()).select_from(DeviceSessionStep)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_policy_revisions_cannot_reset_the_local_grant_budget(
    script_database,
):
    sessions, project, device, _, _, _, _ = script_database
    principal = await prepare(sessions, project, device)
    for revision in (1, 2):
        if revision == 2:
            async with sessions() as db:
                policy = await DeviceScriptPublicationService(db).publish(
                    project, device, request(expected_policy_revision=1)
                )
                principal = principal.model_copy(
                    update={"script_policy_digest": policy.policy.digest}
                )
        async with sessions() as db:
            await ScriptExecutionStore(db).reserve(
                principal, device, uuid4(), {}, descriptor()
            )
    async with sessions() as db:
        policy = await DeviceScriptPublicationService(db).publish(
            project, device, request(expected_policy_revision=2)
        )
        principal = principal.model_copy(
            update={"script_policy_digest": policy.policy.digest}
        )
    async with sessions() as db:
        with pytest.raises(HTTPException) as error:
            await ScriptExecutionStore(db).reserve(
                principal, device, uuid4(), {}, descriptor()
            )
        assert error.value.status_code == 409
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(DeviceScriptExecution)
            )
            == 2
        )


@pytest.mark.asyncio
async def test_failed_receipt_commit_rolls_back_step_counter_and_identity(
    script_database, monkeypatch
):
    sessions, project, device, _, _, _, _ = script_database
    principal = await prepare(sessions, project, device)
    async with sessions() as db:
        monkeypatch.setattr(
            db,
            "commit",
            AsyncMock(side_effect=RuntimeError("owned commit failure")),
        )
        with pytest.raises(RuntimeError, match="owned commit failure"):
            await ScriptExecutionStore(db).reserve(
                principal, device, uuid4(), {}, descriptor()
            )
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(DeviceScriptExecution)
            )
            == 0
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(DeviceSessionStep)
            )
            == 0
        )
        assert (
            await db.get(DeviceSession, principal.session_id)
        ).actions_count == 0


async def prepare(sessions, project, device):
    session, agent = uuid4(), uuid4()
    async with sessions() as db:
        policy = await DeviceScriptPublicationService(db).publish(
            project, device, request()
        )
        db.add(
            DeviceSession(
                id=session,
                device_id=device,
                agent_id=agent,
                agent_name="隔离执行员工",
                status="running",
                started_at=datetime.now(timezone.utc),
                lease_expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=5),
                actions_count=0,
                screenshots_count=0,
                failed_actions_count=0,
            )
        )
        await db.commit()
    return DeviceServicePrincipal(
        sub="tgo-ai",
        project_id=project,
        device_id=device,
        session_id=session,
        script_policy_digest=policy.policy.digest,
    )


@pytest.mark.asyncio
async def test_duplicate_identity_uses_one_budget_and_one_monitor_step(
    script_database,
):
    sessions, project, device, _, _, _, _ = script_database
    principal = await prepare(sessions, project, device)
    execution = uuid4()

    async def reserve():
        async with sessions() as db:
            result, fresh = await ScriptExecutionStore(db).reserve(
                principal, device, execution, {}, descriptor()
            )
            return result.step_id, fresh

    results = await asyncio.wait_for(asyncio.gather(reserve(), reserve()), 30)
    assert sum(fresh for _, fresh in results) == 1
    assert results[0][0] == results[1][0]
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(DeviceScriptExecution)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(DeviceSessionStep)
            )
            == 1
        )
        assert (
            await db.get(DeviceSession, principal.session_id)
        ).actions_count == 1
        row = await db.get(DeviceScriptExecution, (device, execution))
        assert (
            row.status == "unknown" and row.failure == "dispatch_unconfirmed"
        )


@pytest.mark.asyncio
async def test_changed_identity_and_exhausted_budget_do_not_add_steps(
    script_database,
):
    sessions, project, device, _, _, _, _ = script_database
    principal = await prepare(sessions, project, device)
    execution = uuid4()
    item = descriptor().model_copy(
        update={"parameters": descriptor().result_fields}
    )
    # Use a separately reviewed integer input shape; the store cannot accept
    # unknown business fields even when the wire JSON is structurally valid.
    async with sessions() as db:
        row, _ = await ScriptExecutionStore(db).reserve(
            principal, device, execution, {"count": 7}, item
        )
        assert row.parameters_digest and row.context_digest
    for identifier, parameters in (
        (execution, {"count": 8}),
        (uuid4(), {"count": 7}),
    ):
        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                await ScriptExecutionStore(db).reserve(
                    principal, device, identifier, parameters, item
                )
            assert error.value.status_code == 409
    async with sessions() as db:
        data = await db.scalar(
            text(
                "SELECT row_to_json(record)::text "
                "FROM dc_script_executions record"
            )
        )
        assert '"count"' not in data
        assert (
            await db.scalar(
                select(func.count()).select_from(DeviceSessionStep)
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["management", "device", "policy", "lease", "session"]
)
async def test_untrusted_or_inactive_execution_cannot_reserve(
    script_database, invalid
):
    sessions, project, device, _, _, _, _ = script_database
    principal = await prepare(sessions, project, device)
    if invalid == "management":
        principal = principal.model_copy(update={"sub": "tgo-api"})
    elif invalid == "device":
        principal = principal.model_copy(update={"device_id": uuid4()})
    elif invalid == "policy":
        principal = principal.model_copy(
            update={"script_policy_digest": "f" * 64}
        )
    elif invalid == "session":
        principal = principal.model_copy(update={"session_id": uuid4()})
    else:
        async with sessions() as db:
            session = await db.get(DeviceSession, principal.session_id)
            session.lease_expires_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )
            await db.commit()
    async with sessions() as db:
        with pytest.raises(HTTPException):
            await ScriptExecutionStore(db).reserve(
                principal, device, uuid4(), {}, descriptor()
            )
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(DeviceScriptExecution)
            )
            == 0
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(DeviceSessionStep)
            )
            == 0
        )
