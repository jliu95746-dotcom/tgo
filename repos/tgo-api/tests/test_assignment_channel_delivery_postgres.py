"""Isolated PostgreSQL transactions and the real IM client against HTTP."""

import asyncio
import importlib
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.models import (
    Visitor,
    VisitorAssignmentHistory,
    VisitorSession,
)
from app.services import transfer_service
from app.services.wukongim_client import wukongim_client
from app.utils.encoding import build_visitor_channel_id
from tests import test_authentication_boundaries_postgres as postgres_checks

postgres_engine = postgres_checks.postgres_engine
identity = postgres_checks.identity
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not postgres_checks.TEST_URL or not postgres_checks.TEST_SERVER,
        reason="Disposable PostgreSQL and controlled IM HTTP required",
    ),
]


def service():
    return importlib.import_module("app.services.assignment_channel_delivery")


def record(db, operation_id):
    from app.models import AssignmentChannelDelivery

    return db.get(AssignmentChannelDelivery, operation_id)


@pytest_asyncio.fixture
async def reception(identity, monkeypatch):
    db, project, staff, platform = identity
    visitor = Visitor(
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id=uuid4().hex,
        ai_disabled=True,
        service_mode="manual",
        service_status="new",
    )
    db.add(visitor)
    db.flush()
    session = VisitorSession(
        project_id=project.id,
        visitor_id=visitor.id,
        platform_id=platform.id,
        status="open",
    )
    db.add(session)
    db.commit()
    monkeypatch.setattr(wukongim_client, "enabled", True)
    monkeypatch.setattr(
        wukongim_client, "base_url", postgres_checks.TEST_SERVER
    )
    monkeypatch.setattr(wukongim_client, "_http_client", None)
    try:
        yield db, visitor, session, staff
    finally:
        await wukongim_client.aclose()
        db.rollback()


async def statistics(visitor_id):
    async with httpx.AsyncClient(
        base_url=postgres_checks.TEST_SERVER
    ) as client:
        response = await client.get(
            f"/fixture-channel/{build_visitor_channel_id(visitor_id)}",
        )
        response.raise_for_status()
        return response.json()


async def assign(reception, *, commit=False):
    db, visitor, session, staff = reception
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        target_staff_id=staff.id,
        session_id=session.id,
        auto_commit=commit,
        ai_disabled=True,
    )
    assert result.success, result.message
    return result.assignment_history.id


async def test_no_external_changes_before_assignment_commit(reception):
    db, visitor, session, _ = reception
    visitor_id, session_id = visitor.id, session.id
    await assign(reception)
    state = await statistics(visitor_id)
    assert state["calls"] == []
    db.rollback()
    with Session(db.get_bind()) as other:
        assert other.get(VisitorSession, session_id).staff_id is None
        assert (
            other.query(VisitorAssignmentHistory)
            .filter_by(
                visitor_id=visitor_id,
            )
            .count()
            == 0
        )


async def test_committed_channel_recovery_uses_http_without_business_locks(
    reception,
):
    db, visitor, _, staff = reception
    visitor_id, staff_id = visitor.id, staff.id
    operation_id = await assign(reception)
    db.commit()
    assert await service().recover_assignment_channels(db) == 1
    row = record(db, operation_id)
    db.refresh(row)
    assert row.effects_status == "done" and row.status == "sent"
    state = await statistics(visitor_id)
    assert state["members"] == [f"{staff_id}-staff"]
    assert state["calls"] == ["add", "notice"]
    assert state["unlocked"] is True
    assert await service().recover_assignment_channels(db) == 0


async def test_assignment_rollback_never_creates_later_external_work(
    reception,
):
    db, visitor, _, _ = reception
    visitor_id = visitor.id
    operation_id = await assign(reception)
    db.rollback()
    assert record(db, operation_id) is None
    assert await service().recover_assignment_channels(db) == 0
    assert (await statistics(visitor_id))["calls"] == []


@pytest.mark.parametrize(
    "scenario", ["lost_add", "lost_notice", "missing_notice"]
)
async def test_unknown_external_outcome_has_distinct_retry_policy(
    reception, scenario
):
    db, visitor, _, _ = reception
    operation_id = await assign(reception)
    visitor.custom_attributes = {"synthetic_channel_scenario": scenario}
    db.commit()
    assert await service().recover_assignment_channels(db) == 1
    row = record(db, operation_id)
    db.refresh(row)
    if scenario == "lost_add":
        assert row.effects_status == "unknown" and row.status == "pending"
        row.effects_updated_at = datetime.now(timezone.utc) - timedelta(
            minutes=1
        )
    else:
        assert row.effects_status == "done" and row.status == "unknown"
        row.updated_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert await service().recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.effects_status == "done"
    assert row.status == (
        "unknown" if scenario == "missing_notice" else "sent"
    )
    state = await statistics(visitor.id)
    assert state["calls"].count("add") == (2 if scenario == "lost_add" else 1)
    assert state["calls"].count("notice") == 1
    assert (
        db.query(VisitorAssignmentHistory)
        .filter_by(visitor_id=visitor.id)
        .count()
        == 1
    )


async def test_two_independent_workers_send_one_assignment_notice(
    reception, monkeypatch
):
    db, visitor, _, _ = reception
    operation_id = await assign(reception)
    visitor_id, engine = visitor.id, db.get_bind()
    db.commit()
    started, release = Event(), Event()
    real_add = wukongim_client.add_channel_subscribers

    async def pause(**kwargs):
        started.set()
        assert await asyncio.to_thread(release.wait, 10)
        return await real_add(**kwargs)

    monkeypatch.setattr(wukongim_client, "add_channel_subscribers", pause)

    def worker():
        async def run():
            with Session(engine) as other:
                try:
                    return await service().deliver_assignment_channel(
                        other, operation_id
                    )
                finally:
                    await wukongim_client.aclose()

        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(worker)
        try:
            assert await asyncio.to_thread(started.wait, 10)
            with Session(engine) as other:
                assert (
                    await service().deliver_assignment_channel(
                        other, operation_id
                    )
                    == "pending"
                )
        finally:
            release.set()
        assert await asyncio.to_thread(first.result, 15) == "sent"
    state = await statistics(visitor_id)
    assert state["calls"] == ["add", "notice"]


async def test_migration_upgrade_downgrade_and_upgrade_in_isolated_schema(
    postgres_engine,
):
    from app.models import AssignmentChannelDelivery

    schema = "assignment_migration_" + uuid4().hex
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/0082_assignment_channel_delivery.py"
    )
    spec = importlib.util.spec_from_file_location(
        "assignment_delivery_migration", path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0081_agent_knowledge_catalogs"
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            for name in (
                "api_projects",
                "api_visitors",
                "api_visitor_sessions",
                "api_staff",
                "api_visitor_assignment_history",
            ):
                connection.execute(
                    text(f"CREATE TABLE {name} (id uuid PRIMARY KEY)")
                )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
                actual = inspect(connection)
                columns = {
                    column["name"]: column
                    for column in actual.get_columns(
                        "api_assignment_channel_deliveries",
                        schema=schema,
                    )
                }
                expected = AssignmentChannelDelivery.__table__
                assert set(columns) == set(expected.columns.keys())
                assert all(
                    columns[c.name]["nullable"] == c.nullable
                    for c in expected.columns
                )
                assert (
                    len(
                        actual.get_check_constraints(
                            expected.name, schema=schema
                        )
                    )
                    == 2
                )
                assert (
                    len(actual.get_foreign_keys(expected.name, schema=schema))
                    == 5
                )
                assert {
                    index["name"]
                    for index in actual.get_indexes(
                        expected.name, schema=schema
                    )
                } == {index.name for index in expected.indexes}
                migration.downgrade()
                assert not inspect(connection).has_table(
                    expected.name, schema=schema
                )
                migration.upgrade()
                assert inspect(connection).has_table(
                    expected.name, schema=schema
                )
        finally:
            connection.execute(text("SET LOCAL search_path TO public"))
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
