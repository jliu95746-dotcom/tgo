"""Source receipts bind current authority to an exact committed close fact."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, inspect

from app.api.internal.endpoints.workflow_event_sources import router as internal_router
from app.api.v1.endpoints.workflow_events import router as public_router
from app.api import company_configuration_access as configuration_access
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.core.security import get_current_active_user
from app.models.channel_memory_clearance import ChannelMemoryClearance
from app.models.company_account import CompanyAccount
from app.models.session_command_delivery import SessionCommandDelivery
from app.models.visitor_session import VisitorSession
from app.models.workflow_event_subscription import (
    WorkflowEventDelivery,
    WorkflowEventSubscription,
)
from app.schemas.workflow_event_subscriptions import (
    WorkflowEventSourceBinding,
    WorkflowEventSourceCheck,
)
from app.services.workflow_event_subscriptions import verify_source_binding
from tests.test_im_access import records  # noqa: F401


@pytest.fixture
def source(records):  # noqa: F811
    db, projects, staff, platforms, visitors = records
    for model in (
        VisitorSession,
        SessionCommandDelivery,
        ChannelMemoryClearance,
        WorkflowEventSubscription,
        WorkflowEventDelivery,
    ):
        model.__table__.create(db.get_bind())
    now = datetime.now(timezone.utc)
    version = now.replace(tzinfo=None)
    session = VisitorSession(
        id=uuid4(),
        project_id=projects[0].id,
        visitor_id=visitors[0].id,
        platform_id=platforms[0].id,
        staff_id=staff[0].id,
        status="closed",
        created_at=version - timedelta(minutes=5),
        closed_at=version,
        updated_at=version,
        last_message_seq=50,
    )
    command = SessionCommandDelivery(
        id=uuid4(),
        project_id=projects[0].id,
        visitor_id=visitors[0].id,
        session_id=session.id,
        actor_staff_id=staff[0].id,
        kind="close",
        session_version=version,
        actor_role="admin",
        actor_token_version=1,
        actor_token_expires_at=now - timedelta(hours=1),
        client_msg_no=uuid4().hex,
        channel_id=str(visitors[0].id),
        channel_type=251,
        from_uid="actor",
        payload={},
    )
    sub = WorkflowEventSubscription(
        id=uuid4(),
        project_id=projects[0].id,
        workflow_id="flow",
        enabled=True,
        revision=1,
        release_id="release",
        release_digest="a" * 64,
        version=1,
        enabled_at=now - timedelta(minutes=1),
        enabled_by_staff_id=staff[0].id,
    )
    delivery = WorkflowEventDelivery(
        id=uuid4(),
        subscription_id=sub.id,
        subscription_revision=1,
        project_id=projects[0].id,
        workflow_id="flow",
        source_command_id=command.id,
        session_id=session.id,
        release_id="release",
        release_digest="a" * 64,
    )
    db.add_all([session, command, sub, delivery])
    db.commit()
    query = WorkflowEventSourceCheck(
        project_id=projects[0].id,
        workflow_id="flow",
        release_id="release",
        release_digest="a" * 64,
        source_binding=WorkflowEventSourceBinding(
            subscription_id=sub.id,
            revision=1,
            delivery_id=delivery.id,
            source_command_id=command.id,
            session_id=session.id,
        ),
    )
    return (
        db,
        staff[0],
        platforms[0],
        visitors[0],
        session,
        command,
        sub,
        delivery,
        query,
    )


def test_close_fact_survives_notice_failure_and_actor_token_expiry(source):
    db, _, _, _, _, command, _, _, query = source
    command.status = "unknown"
    db.commit()
    assert verify_source_binding(db, query) == query


@pytest.mark.parametrize(
    "mutation",
    [
        "tenant",
        "workflow",
        "revision",
        "release",
        "source",
        "disabled",
        "admin",
        "deleted_admin",
        "visitor_retired",
        "platform_disabled",
        "session_changed",
        "old_event",
        "cancelled",
        "cleared",
    ],
)
def test_current_source_must_still_be_authorized(source, mutation):
    db, staff, platform, visitor, session, command, sub, delivery, query = source
    if mutation == "tenant":
        query.project_id = uuid4()
    elif mutation == "workflow":
        query.workflow_id = "other"
    elif mutation == "revision":
        query.source_binding.revision = 2
    elif mutation == "release":
        query.release_digest = "b" * 64
    elif mutation == "source":
        query.source_binding.source_command_id = uuid4()
    elif mutation == "disabled":
        sub.enabled = False
    elif mutation == "admin":
        staff.role = "user"
    elif mutation == "deleted_admin":
        staff.deleted_at = datetime.now()
    elif mutation == "visitor_retired":
        visitor.access_retired_at = datetime.now(timezone.utc)
    elif mutation == "platform_disabled":
        platform.is_active = False
    elif mutation == "session_changed":
        session.updated_at += timedelta(seconds=1)
    elif mutation == "old_event":
        command.created_at = sub.enabled_at - timedelta(seconds=1)
    elif mutation == "cancelled":
        delivery.status = "cancelled"
    else:
        db.add(
            ChannelMemoryClearance(
                project_id=query.project_id,
                user_id=staff.id,
                user_type="staff",
                channel_id=command.channel_id,
                channel_type=251,
                cleared_message_seq=45,
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(seconds=1),
            )
        )
    db.commit()
    with pytest.raises(HTTPException) as rejected:
        verify_source_binding(db, query)
    assert rejected.value.status_code == 409


@pytest.mark.asyncio
async def test_private_source_receipt_requires_service_key(source):
    db, *_, query = source
    app = FastAPI()
    app.include_router(internal_router, prefix="/internal/workflows/events")
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = query.model_dump(mode="json")
        denied = await client.post(
            "/internal/workflows/events/source/verify", json=body
        )
        assert denied.status_code == 403
        accepted = await client.post(
            "/internal/workflows/events/source/verify",
            json=body,
            headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        assert accepted.status_code == 200 and accepted.json() == body
        assert accepted.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_public_controls_use_current_tenant_admin_and_hide_payload(source):
    db, staff, *_, delivery, query = source
    app = FastAPI()
    app.include_router(public_router, prefix="/workflows")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: staff
    delivery.encrypted_payload = "never-expose-this"
    db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        listed = await client.get("/workflows/flow/event-deliveries")
        assert listed.status_code == 200
        assert (
            "encrypted_payload" not in listed.text and "never-expose" not in listed.text
        )
        saved = await client.put(
            "/workflows/flow/event-subscription",
            json={"enabled": False, "expected_revision": 1},
        )
        assert saved.status_code == 200 and saved.json()["revision"] == 2
        staff.role = "user"
        db.commit()
        denied = await client.get("/workflows/flow/event-subscription")
        assert denied.status_code == 403


def test_source_migration_creates_matching_columns_and_rolls_back(monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/0079_workflow_event_sources.py"
    spec = importlib.util.spec_from_file_location("source_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        for model in (WorkflowEventSubscription, WorkflowEventDelivery):
            actual = {
                c["name"]: c
                for c in inspect(connection).get_columns(model.__tablename__)
            }
            assert set(actual) == set(model.__table__.columns.keys())
            for column in model.__table__.columns:
                assert actual[column.name]["nullable"] == column.nullable
        migration.downgrade()
        assert not inspect(connection).has_table("api_workflow_event_subscriptions")
        assert not inspect(connection).has_table("api_workflow_event_deliveries")
    engine.dispose()


@pytest.mark.parametrize("with_delivery", [False, True])
def test_source_migration_refuses_to_drop_existing_audit_records(monkeypatch, with_delivery):
    path = Path(__file__).parents[1] / "alembic/versions/0079_workflow_event_sources.py"
    spec = importlib.util.spec_from_file_location("source_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        sub_id, project_id = uuid4(), uuid4()
        connection.execute(WorkflowEventSubscription.__table__.insert().values(
            id=sub_id, project_id=project_id, workflow_id="flow"))
        if with_delivery:
            connection.execute(WorkflowEventDelivery.__table__.insert().values(
                subscription_id=sub_id, subscription_revision=1, project_id=project_id,
                workflow_id="flow", source_command_id=uuid4(), session_id=uuid4(),
                release_id="release", release_digest="a" * 64))
        with pytest.raises(RuntimeError, match="保留兼容代码"):
            migration.downgrade()
        assert inspect(connection).has_table("api_workflow_event_subscriptions")
        assert inspect(connection).has_table("api_workflow_event_deliveries")
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("company_status", ["expired", "suspended"])
async def test_paused_company_stops_automation_but_can_disable_with_real_outer_gate(source, monkeypatch, company_status):
    from fastapi import Depends
    from app.services.workflow_event_subscriptions import current_subscription_authorized

    db, staff, _platform, _visitor, _session, _command, sub, _delivery, query = source
    CompanyAccount.__table__.create(db.get_bind())
    db.add(CompanyAccount(project_id=staff.project_id, status=company_status))
    db.commit()
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(configuration_access, "resolve_staff_token", lambda _db, _token: staff)
    assert not current_subscription_authorized(db, sub)
    with pytest.raises(HTTPException) as revoked:
        verify_source_binding(db, query)
    assert revoked.value.status_code == 409
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(public_router, prefix="/workflows", dependencies=[Depends(configuration_access.require_configuration_write)])
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: staff
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": "Bearer synthetic-test-session"}) as client:
        blocked = await client.put("/workflows/flow/event-subscription", json={
            "enabled": True, "expected_revision": 1,
            "expected_release_id": "release", "expected_release_digest": "a" * 64})
        assert blocked.status_code == 402
        closed = await client.delete("/workflows/flow/event-subscription?expected_revision=1")
        assert closed.status_code == 200 and closed.json()["enabled"] is False
        assert closed.json()["revision"] == 2
