"""Current tenant authority and revision binding for production events."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.staff import Staff
from app.models.workflow_event_subscription import (
    WorkflowEventSubscription,
    WorkflowEventDelivery,
)
from app.schemas.workflow_event_subscriptions import WorkflowEventSubscriptionUpdate
from app.schemas.workflow_events import WorkflowEventTestContext
from app.services import workflow_event_subscriptions as service


@pytest.fixture
def store():
    engine = create_engine("sqlite://")
    for table in (
        Project.__table__,
        Staff.__table__,
        WorkflowEventSubscription.__table__,
        WorkflowEventDelivery.__table__,
    ):
        table.create(engine)
    with Session(engine) as db:
        project = Project(id=uuid4(), name="test", api_key=uuid4().hex)
        staff = Staff(
            id=uuid4(),
            project_id=project.id,
            username=uuid4().hex,
            password_hash="unused",
            role="admin",
            account_enabled=True,
        )
        db.add_all([project, staff])
        db.commit()
        yield db, staff
    engine.dispose()


def context():
    return WorkflowEventTestContext(
        workflow_id="flow",
        event_type="staff.session.closed",
        release_id="release-1",
        release_digest="a" * 64,
        version=1,
        status="active",
    )


def enable(revision=0):
    return WorkflowEventSubscriptionUpdate(
        enabled=True,
        expected_revision=revision,
        expected_release_id="release-1",
        expected_release_digest="a" * 64,
    )


@pytest.mark.asyncio
async def test_enable_is_explicit_and_binds_current_release(store, monkeypatch):
    db, actor = store
    monkeypatch.setattr(
        service.workflow_client,
        "get_event_test_context",
        AsyncMock(return_value=context()),
    )
    assert not service.get_subscription(db, actor.project_id, "flow").enabled
    saved = await service.update_subscription(db, actor, "flow", enable())
    assert saved.enabled and saved.revision == 1
    assert saved.release_id == "release-1" and saved.enabled_at
    with pytest.raises(HTTPException) as conflict:
        await service.update_subscription(db, actor, "flow", enable())
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_disable_does_not_require_healthy_workflow_and_revokes_pending(
    store, monkeypatch
):
    db, actor = store
    remote = AsyncMock(return_value=context())
    monkeypatch.setattr(service.workflow_client, "get_event_test_context", remote)
    saved = await service.update_subscription(db, actor, "flow", enable())
    row = WorkflowEventDelivery(
        subscription_id=saved.subscription_id,
        subscription_revision=1,
        project_id=actor.project_id,
        workflow_id="flow",
        source_command_id=uuid4(),
        session_id=uuid4(),
        release_id="release-1",
        release_digest="a" * 64,
        encrypted_payload="sensitive-frozen-payload",
    )
    db.add(row)
    db.commit()
    remote.side_effect = HTTPException(503, "unavailable")
    disabled = await service.update_subscription(
        db,
        actor,
        "flow",
        WorkflowEventSubscriptionUpdate(enabled=False, expected_revision=1),
    )
    assert not disabled.enabled and disabled.revision == 2
    db.refresh(row)
    assert row.status == "cancelled" and row.encrypted_payload is None
    assert remote.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["release", "event", "workflow"])
async def test_enable_rejects_wrong_publication(store, monkeypatch, change):
    db, actor = store
    publication = context()
    if change == "release":
        publication.release_digest = "b" * 64
    elif change == "event":
        publication.event_type = "anything.else"
    else:
        publication.workflow_id = "other"
    monkeypatch.setattr(
        service.workflow_client,
        "get_event_test_context",
        AsyncMock(return_value=publication),
    )
    with pytest.raises(HTTPException) as rejected:
        await service.update_subscription(db, actor, "flow", enable())
    assert rejected.value.status_code == 409
    assert not service.get_subscription(db, actor.project_id, "flow").enabled


@pytest.mark.asyncio
async def test_current_admin_disable_and_tenant_isolation(store, monkeypatch):
    db, actor = store
    monkeypatch.setattr(
        service.workflow_client,
        "get_event_test_context",
        AsyncMock(return_value=context()),
    )
    saved = await service.update_subscription(db, actor, "flow", enable())
    row = db.get(WorkflowEventSubscription, saved.subscription_id)
    assert service.current_subscription_authorized(db, row)
    assert not service.get_subscription(db, uuid4(), "flow").enabled
    actor.account_enabled = False
    db.commit()
    assert not service.current_subscription_authorized(db, row)
    actor.account_enabled = True
    actor.role = "user"
    db.commit()
    assert not service.current_subscription_authorized(db, row)
    with pytest.raises(HTTPException) as denied:
        await service.update_subscription(db, actor, "flow", enable(1))
    assert denied.value.status_code == 403


def test_enable_requires_release_pair_but_disable_does_not():
    with pytest.raises(ValueError):
        WorkflowEventSubscriptionUpdate(enabled=True, expected_revision=0)
    WorkflowEventSubscriptionUpdate(enabled=False, expected_revision=0)
