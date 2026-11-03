"""Opt-in PostgreSQL race coverage for a manual session close."""

import asyncio
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.core.security import create_access_token
from app.models import (
    AIProvider,
    AssignmentSource,
    ChannelMember,
    Platform,
    Project,
    SessionCommandDelivery,
    Staff,
    Visitor,
    VisitorAssignmentHistory,
    VisitorAssignmentRule,
    VisitorSession,
)
from app.services import (
    session_command_delivery,
    session_service,
    transfer_service,
    visitor_chat_access,
)
from app.services.staff_session_access import begin_staff_session_change


TEST_URL = os.getenv("SCENARIOS_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="An explicitly isolated PostgreSQL database is required"
)


def delivery_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/0052_session_command_deliveries.py"
    )
    spec = importlib.util.spec_from_file_location("session_delivery_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def effects_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/0053_session_command_channel_effects.py"
    )
    spec = importlib.util.spec_from_file_location("session_effects_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def owned_database(monkeypatch):
    assert TEST_URL is not None
    url = make_url(TEST_URL)
    assert (url.database or "").startswith("tgo_scenarios_test_")
    assert url.host in {"127.0.0.1", "localhost"} and url.port == 15432
    schema = "staff_session_race_" + uuid4().hex
    engine = create_engine(
        url.set(drivername="postgresql+psycopg2"),
        connect_args={"options": "-csearch_path=" + schema},
    )
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            for model in (
                Project,
                Staff,
                Platform,
                Visitor,
                VisitorSession,
                ChannelMember,
                AIProvider,
                VisitorAssignmentRule,
                VisitorAssignmentHistory,
            ):
                model.__table__.create(connection)
            with Operations.context(MigrationContext.configure(connection)):
                delivery_migration().upgrade()
                connection.execute(text(
                    "CREATE TABLE api_alembic_version "
                    "(version_num VARCHAR(32) NOT NULL)"
                ))
                connection.execute(text(
                    "INSERT INTO api_alembic_version (version_num) "
                    "VALUES ('0052_session_command_deliveries')"
                ))
                effects_migration().upgrade()
                connection.execute(
                    text("UPDATE api_alembic_version SET version_num = :revision"),
                    {"revision": effects_migration().revision},
                )
                assert connection.scalar(text(
                    "SELECT version_num FROM api_alembic_version"
                )) == effects_migration().revision
            assert inspect(connection).has_table(
                "api_session_command_deliveries", schema=schema
            )

        monkeypatch.setattr(
            visitor_chat_access, "SessionLocal", lambda: Session(engine)
        )
        monkeypatch.setattr(
            session_service.wukongim_client,
            "remove_channel_subscribers",
            AsyncMock(),
        )
        notice = AsyncMock(return_value=SimpleNamespace(message_id="pg-receipt"))
        monkeypatch.setattr(
            session_command_delivery.wukongim_client, "send_message", notice
        )
        monkeypatch.setattr(session_command_delivery.wukongim_client, "enabled", True)
        monkeypatch.setattr(
            session_service.wukongim_client, "delete_conversation", AsyncMock()
        )
        monkeypatch.setattr(session_service, "trigger_queue_for_staff", AsyncMock())

        project_id, staff_id, platform_id, visitor_id, session_id = (
            uuid4() for _ in range(5)
        )
        with Session(engine) as db:
            db.add(Project(id=project_id, name="session-race", api_key="fixture-only"))
            db.commit()
            db.add_all(
                [
                    Staff(
                        id=staff_id,
                        project_id=project_id,
                        username="session-race-admin",
                        password_hash="unused",
                        role="admin",
                        account_enabled=True,
                    ),
                    Platform(
                        id=platform_id,
                        project_id=project_id,
                        type="website",
                        name="隔离会话渠道",
                        is_active=True,
                    ),
                ]
            )
            db.commit()
            db.add(
                Visitor(
                    id=visitor_id,
                    project_id=project_id,
                    platform_id=platform_id,
                    platform_open_id="fixture-only-visitor",
                )
            )
            db.commit()
            db.add(
                VisitorSession(
                    id=session_id,
                    project_id=project_id,
                    visitor_id=visitor_id,
                    platform_id=platform_id,
                    staff_id=staff_id,
                    status="open",
                )
            )
            db.commit()
        yield engine, project_id, staff_id, session_id, notice
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_two_transactions_cannot_close_the_same_session_twice(
    owned_database, monkeypatch
):
    engine, project_id, staff_id, session_id, notice = owned_database
    barrier = Barrier(2)

    async def wait_for_both_commands(**_kwargs):
        barrier.wait(timeout=10)
        return None

    monkeypatch.setattr(
        session_service.wukongim_client,
        "get_channel_last_message",
        AsyncMock(side_effect=wait_for_both_commands),
    )
    token = create_access_token("session-race-admin", project_id, "admin")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    def close_once() -> str:
        with Session(engine) as db:
            actor = db.get(Staff, staff_id)
            assert actor is not None
            session, scope = begin_staff_session_change(
                db, credentials, actor, session_id=session_id
            )
            try:
                operation_id = uuid4()
                result = asyncio.run(
                    session_service.close_visitor_session(
                        db,
                        session,
                        actor,
                        command_scope=scope,
                        operation_id=operation_id,
                    )
                )
                intent = db.get(SessionCommandDelivery, operation_id)
                assert intent is not None and intent.status == "pending"
                assert (
                    asyncio.run(
                        session_command_delivery.deliver_session_command(
                            db, operation_id
                        )
                    )
                    == "sent"
                )
                return "closed" if result.id == session_id else "wrong-session"
            except TGOAPIException as exc:
                return f"rejected:{exc.status_code}"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: close_once(), range(2)))
    assert sorted(results) == ["closed", "rejected:409"]
    with Session(engine) as db:
        session = db.get(VisitorSession, session_id)
        assert session is not None and session.status == "closed"
        assert db.query(SessionCommandDelivery).count() == 1
    notice.assert_awaited_once()


def test_target_disabled_after_assignment_selection_is_not_committed(
    owned_database, monkeypatch
):
    engine, project_id, staff_id, session_id, _ = owned_database
    target_id = uuid4()
    with Session(engine) as db:
        db.add(
            Staff(
                id=target_id,
                project_id=project_id,
                username="session-race-target",
                password_hash="unused",
                role="user",
                account_enabled=True,
                is_active=True,
                service_paused=False,
            )
        )
        db.commit()

    async def disable_target(**_kwargs):
        with Session(engine) as other:
            target = other.get(Staff, target_id)
            assert target is not None
            target.is_active = False
            other.commit()
        return transfer_service.StaffAssignmentResult(
            assigned_staff_id=target_id, candidate_staff_ids=[target_id]
        )

    monkeypatch.setattr(
        transfer_service, "assign_staff", AsyncMock(side_effect=disable_target)
    )
    add_to_channel = AsyncMock()
    monkeypatch.setattr(transfer_service, "_add_staff_to_channel", add_to_channel)
    token = create_access_token("session-race-admin", project_id, "admin")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with Session(engine) as db:
        actor = db.get(Staff, staff_id)
        assert actor is not None
        session, scope = begin_staff_session_change(
            db, credentials, actor, session_id=session_id
        )
        with pytest.raises(TGOAPIException) as error:
            asyncio.run(
                transfer_service.transfer_to_staff(
                    db=db,
                    visitor_id=session.visitor_id,
                    project_id=project_id,
                    source=AssignmentSource.TRANSFER,
                    assigned_by_staff_id=staff_id,
                    target_staff_id=target_id,
                    session_id=session_id,
                    session_scope=scope,
                    platform_id=session.platform_id,
                    skip_queue_status_check=True,
                    send_notification=False,
                )
            )
        assert error.value.status_code == 409
    with Session(engine) as db:
        session = db.get(VisitorSession, session_id)
        assert session is not None and session.staff_id == staff_id
        assert db.query(VisitorAssignmentHistory).count() == 0
        assert db.query(SessionCommandDelivery).count() == 0
    add_to_channel.assert_not_awaited()
