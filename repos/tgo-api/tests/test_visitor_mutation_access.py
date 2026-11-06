"""Actual staff writes must preserve their original authority and customer."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles

from app.api.v1.endpoints import visitors as endpoints
from app.core.config import settings
from app.core.security import create_access_token
from app.models import Visitor, VisitorSession, Permission, RolePermission
from app.core.exceptions import TGOAPIException
from app.schemas.visitor import VisitorAttributesUpdate
from tests.test_im_access import records as records
from tests.test_visitor_profile_access import profile_api as profile_api
from tests.test_staff_visitor_read_access import staff_read_api as staff_read_api


@compiles(ARRAY, "sqlite")
def compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def mutation_api(staff_read_api, monkeypatch):
    app, db, projects, staff, platforms, visitors, extra_staff, _ = staff_read_api
    notify = AsyncMock()
    monkeypatch.setattr(endpoints, "notify_visitor_profile_updated", notify)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    return app, db, projects, staff, platforms, visitors, extra_staff, notify


def proof(staff):
    return {"Authorization": "Bearer " + create_access_token(staff.username, staff.project_id, staff.role)}


async def send(app, method, path, headers, body=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers=headers, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["missing", "deleted", "foreign"])
async def test_attributes_never_create_restore_or_adopt_unknown_customer(mutation_api, state):
    app, db, _, staff, _, visitors, _, notify = mutation_api
    target = uuid4() if state == "missing" else visitors[1].id if state == "foreign" else visitors[0].id
    if state == "deleted":
        visitors[0].deleted_at = datetime.utcnow()
        db.commit()
    before = db.query(Visitor).count()
    response = await send(app, "PUT", f"/visitors/{target}/attributes", proof(staff[0]), {"note": "must-not-create"})
    assert response.status_code == 404
    assert db.query(Visitor).count() == before
    notify.assert_not_awaited()
    if state == "deleted":
        db.refresh(visitors[0])
        assert visitors[0].deleted_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["missing", "foreign", "inactive"])
async def test_staff_create_requires_real_active_owned_platform(mutation_api, platform):
    app, db, _, staff, platforms, _, _, notify = mutation_api
    target = uuid4() if platform == "missing" else platforms[1].id if platform == "foreign" else platforms[0].id
    if platform == "inactive":
        platforms[0].is_active = False
        db.commit()
    before = db.query(Visitor).count()
    response = await send(app, "POST", "/visitors", proof(staff[0]), {"platform_id": str(target), "platform_open_id": "must-not-create"})
    assert response.status_code == 404
    assert db.query(Visitor).count() == before
    notify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["attributes", "patch", "enable", "disable", "mode", "delete"])
async def test_authorized_customer_writes_and_no_store(mutation_api, operation):
    app, db, _, staff, _, visitors, _, notify = mutation_api
    base = f"/visitors/{visitors[0].id}"
    method, path, body = {
        "attributes": ("PUT", base + "/attributes", {"note": "new-note"}),
        "patch": ("PATCH", base, {"name": "new-name"}),
        "enable": ("POST", base + "/enable-ai", None),
        "disable": ("POST", base + "/disable-ai", None),
        "mode": ("PUT", base + "/service-mode", {"service_mode": "manual"}),
        "delete": ("DELETE", base, None),
    }[operation]
    response = await send(app, method, path, proof(staff[0]), body)
    assert response.status_code == (204 if operation == "delete" else 200)
    assert response.headers["cache-control"] == "no-store"
    db.refresh(visitors[0])
    if operation == "attributes":
        assert visitors[0].note == "new-note"
    if operation == "patch":
        assert visitors[0].name == "new-name"
    if operation == "delete":
        assert visitors[0].deleted_at is not None
    if operation != "delete":
        notify.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoked", "permission", "owner", "platform", "version"])
async def test_service_mode_wait_rechecks_before_mutating_database(mutation_api, monkeypatch, change):
    app, db, _, staff, platforms, visitors, extra, notify = mutation_api
    staff[0].role = "user"
    permission = Permission(resource="visitors", action="update")
    db.add(permission)
    db.flush()
    grant = RolePermission(role="user", permission_id=permission.id)
    db.add(grant)
    db.commit()
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    headers = proof(staff[0])

    async def changed(*_args):
        if change == "revoked":
            staff[0].account_enabled = False
        elif change == "permission":
            db.delete(grant)
        elif change == "owner":
            db.query(VisitorSession).filter(VisitorSession.visitor_id == visitors[0].id).one().staff_id = extra.id
        elif change == "platform":
            visitors[0].platform_id = platforms[1].id
        else:
            visitors[0].note = "another-writer"
            visitors[0].updated_at = datetime.utcnow() + timedelta(seconds=1)
        db.commit()
        return "published prompt"

    monkeypatch.setattr(endpoints.visitor_service, "get_humanization_skill_prompt", AsyncMock(side_effect=changed))
    response = await send(app, "PUT", f"/visitors/{visitors[0].id}/service-mode", headers,
        {"service_mode": "assist", "humanization_skill_name": "friendly", "humanization_skill_enabled": True})
    assert response.status_code == {"revoked": 401, "permission": 403, "owner": 404, "platform": 404, "version": 409}[change]
    db.refresh(visitors[0])
    assert visitors[0].service_mode is None and visitors[0].humanization_skill_name == "private-skill"
    notify.assert_not_awaited()
    if change == "version":
        assert visitors[0].note == "another-writer"


@pytest.mark.asyncio
async def test_attribute_revocation_during_payload_preparation_blocks_commit(mutation_api, monkeypatch):
    app, db, _, staff, _, visitors, _, notify = mutation_api
    original = VisitorAttributesUpdate.model_dump

    def revoke(self, *args, **kwargs):
        values = original(self, *args, **kwargs)
        staff[0].token_version += 1
        db.commit()
        return values

    monkeypatch.setattr(VisitorAttributesUpdate, "model_dump", revoke)
    response = await send(app, "PUT", f"/visitors/{visitors[0].id}/attributes", proof(staff[0]), {"note": "must-not-commit"})
    assert response.status_code == 401
    db.refresh(visitors[0])
    assert visitors[0].note == "staff-private-note"
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoke_during_notification_does_not_release_old_response_or_undo_committed_change(mutation_api):
    app, db, _, staff, _, visitors, _, notify = mutation_api

    async def revoke(*_args):
        staff[0].account_enabled = False
        db.commit()

    notify.side_effect = revoke
    response = await send(app, "PATCH", f"/visitors/{visitors[0].id}", proof(staff[0]), {"note": "authorized-committed-change"})
    assert response.status_code == 401 and "authorized-committed-change" not in response.text
    db.refresh(visitors[0])
    assert visitors[0].note == "authorized-committed-change"


@pytest.mark.asyncio
async def test_soft_delete_cannot_erase_later_customer_state(mutation_api, monkeypatch):
    from app.services import visitor_mutation_access

    _, db, _, staff, _, visitors, _, _ = mutation_api
    from fastapi.security import HTTPAuthorizationCredentials
    scope = visitor_mutation_access.begin_mutation(db,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=proof(staff[0])["Authorization"][7:]),
        staff[0], visitors[0].id, "visitors:delete")
    visitors[0].note = "later-change"
    visitors[0].updated_at = datetime.utcnow() + timedelta(seconds=1)
    db.commit()
    with pytest.raises(TGOAPIException) as failure:
        visitor_mutation_access.commit_change(db, scope, lambda visitor: setattr(visitor, "deleted_at", datetime.utcnow()))
    assert failure.value.status_code == 409
    db.refresh(visitors[0])
    assert visitors[0].deleted_at is None and visitors[0].note == "later-change"


@pytest.fixture
def claim_api(mutation_api, monkeypatch):
    from app.models import AssignmentChannelDelivery, VisitorAssignmentHistory, VisitorAssignmentRule, VisitorWaitingQueue, ChannelMember
    from app.services import transfer_service, queue_events
    app, db, _, _, _, visitors, _, _ = mutation_api
    for model in (VisitorAssignmentHistory, VisitorAssignmentRule, VisitorWaitingQueue, ChannelMember, AssignmentChannelDelivery):
        model.__table__.create(db.get_bind(), checkfirst=True)
    db.query(VisitorSession).filter(VisitorSession.visitor_id == visitors[0].id).one().staff_id = None
    db.commit()
    monkeypatch.setattr(queue_events, "publish_queue_updates", AsyncMock())
    dispatch = AsyncMock()
    monkeypatch.setattr(transfer_service, "deliver_assignment_channel", dispatch)
    return (*mutation_api, dispatch)


@pytest.mark.asyncio
async def test_manual_claim_assigns_once_with_current_original_member(claim_api):
    app, db, _, staff, _, visitors, _, _, dispatch = claim_api
    response = await send(app, "POST", f"/visitors/{visitors[0].id}/accept", proof(staff[0]))
    assert response.status_code == 200 and response.json()["staff_id"] == str(staff[0].id)
    assert response.headers["cache-control"] == "no-store"
    session = db.query(VisitorSession).filter(VisitorSession.visitor_id == visitors[0].id).one()
    assert session.staff_id == staff[0].id
    dispatch.assert_awaited_once()
    repeated = await send(app, "POST", f"/visitors/{visitors[0].id}/accept", proof(staff[0]))
    assert repeated.status_code in (400, 409)
    assert dispatch.await_count == 1


@pytest.mark.asyncio
async def test_claim_revoked_while_selecting_member_does_not_commit_assignment(claim_api, monkeypatch):
    from app.services import transfer_service
    from app.models import VisitorAssignmentHistory
    app, db, _, staff, _, visitors, _, _, dispatch = claim_api

    async def select(**_kwargs):
        staff[0].account_enabled = False
        db.commit()
        return transfer_service.StaffAssignmentResult(assigned_staff_id=staff[0].id, candidate_staff_ids=[staff[0].id])

    monkeypatch.setattr(transfer_service, "assign_staff", AsyncMock(side_effect=select))
    response = await send(app, "POST", f"/visitors/{visitors[0].id}/accept", proof(staff[0]))
    assert response.status_code == 401
    db.refresh(visitors[0])
    assert visitors[0].service_status == "new"
    assert db.query(VisitorSession).filter(VisitorSession.visitor_id == visitors[0].id).one().staff_id is None
    assert db.query(VisitorAssignmentHistory).count() == 0
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_revoked_during_channel_dispatch_keeps_committed_assignment_but_blocks_reply(claim_api):
    app, db, _, staff, _, visitors, _, _, dispatch = claim_api

    async def revoke(*_args, **_kwargs):
        staff[0].token_version += 1
        db.commit()

    dispatch.side_effect = revoke
    response = await send(app, "POST", f"/visitors/{visitors[0].id}/accept", proof(staff[0]))
    assert response.status_code == 401 and "session_id" not in response.text
    db.refresh(visitors[0])
    assert visitors[0].service_status == "active"
    assert db.query(VisitorSession).filter(VisitorSession.visitor_id == visitors[0].id).one().staff_id == staff[0].id


@pytest.mark.asyncio
async def test_channel_add_revoke_blocks_following_assigned_notification(mutation_api, monkeypatch):
    from app.services import assignment_channel_delivery, visitor_mutation_access
    from app.services.wukongim_client import wukongim_client
    from fastapi.security import HTTPAuthorizationCredentials
    from app.models import AssignmentChannelDelivery, ChannelMember, VisitorAssignmentHistory, VisitorAssignmentRule

    _, db, _, staff, _, visitors, _, _ = mutation_api
    for model in (ChannelMember, VisitorAssignmentRule, VisitorAssignmentHistory, AssignmentChannelDelivery):
        model.__table__.create(db.get_bind(), checkfirst=True)
    scope = visitor_mutation_access.begin_mutation(db,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=proof(staff[0])["Authorization"][7:]),
        staff[0], visitors[0].id, "visitors:update")
    message = AsyncMock()
    session = db.query(VisitorSession).filter_by(visitor_id=visitors[0].id).one()
    session.platform_id = visitors[0].platform_id
    history = VisitorAssignmentHistory(
        project_id=visitors[0].project_id, visitor_id=visitors[0].id,
        session_id=session.id, assigned_staff_id=staff[0].id, source="manual",
    )
    visitors[0].ai_disabled = True
    db.add(history)
    db.flush()
    operation_id = assignment_channel_delivery.stage_assignment_channel(
        db, history, send_notification=True, ai_disabled=True,
        access=scope.access, permission=scope.permission,
    )
    db.commit()

    async def revoke(**_kwargs):
        staff[0].token_version += 1
        db.commit()

    monkeypatch.setattr(wukongim_client, "enabled", True)
    monkeypatch.setattr(wukongim_client, "add_channel_subscribers", AsyncMock(side_effect=revoke))
    monkeypatch.setattr(wukongim_client, "send_message", message)
    assert await assignment_channel_delivery.deliver_assignment_channel(db, operation_id) == "skipped"
    with pytest.raises(TGOAPIException):
        scope.guard().check()
    message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["customer", "session"])
async def test_real_claim_stops_notice_when_context_changes_in_im(
    claim_api, monkeypatch, change,
):
    from app.models import AssignmentChannelDelivery
    from app.services import assignment_channel_delivery, transfer_service
    from app.services.wukongim_client import wukongim_client

    app, db, _, staff, _, visitors, _, _, _ = claim_api
    session = db.query(VisitorSession).filter_by(
        visitor_id=visitors[0].id,
    ).one()

    async def change_context(**_kwargs):
        target = visitors[0] if change == "customer" else session
        target.updated_at = datetime.utcnow() + timedelta(seconds=1)
        db.commit()

    monkeypatch.setattr(
        transfer_service, "deliver_assignment_channel",
        assignment_channel_delivery.deliver_assignment_channel,
    )
    monkeypatch.setattr(wukongim_client, "enabled", True)
    monkeypatch.setattr(
        wukongim_client, "add_channel_subscribers",
        AsyncMock(side_effect=change_context),
    )
    message = AsyncMock()
    monkeypatch.setattr(wukongim_client, "send_message", message)
    response = await send(
        app, "POST", f"/visitors/{visitors[0].id}/accept", proof(staff[0]),
    )
    # Direct claim guards the customer version; a same-owner session timestamp
    # change stops the old notice without invalidating the claimed owner.
    assert response.status_code == (409 if change == "customer" else 200)
    if change == "customer":
        assert "session_id" not in response.text
    db.expire_all()
    row = db.query(AssignmentChannelDelivery).one()
    assert row.effects_status == "done" and row.status == "skipped"
    assert session.staff_id == staff[0].id
    message.assert_not_awaited()
