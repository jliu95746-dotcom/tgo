"""Legacy queue commands must use the original staff, wait and session."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import visitor_waiting_queue
from app.models import VisitorSession, VisitorWaitingQueue, VisitorAssignmentHistory
from app.services import transfer_service
from tests.test_im_access import records as records
from tests.test_visitor_profile_access import profile_api as profile_api
from tests.test_staff_visitor_read_access import staff_read_api as staff_read_api
from tests.test_visitor_mutation_access import mutation_api as mutation_api, claim_api as claim_api, proof, send


@pytest.fixture
def queue_api(claim_api):
    app, db, _, staff, _, visitors, _, _, dispatch = claim_api
    app.include_router(visitor_waiting_queue.router, prefix="/waiting-queue")
    session = db.query(VisitorSession).filter(VisitorSession.visitor_id == visitors[0].id).one()
    visitors[0].service_status = "queued"
    session.platform_id = visitors[0].platform_id
    entry = VisitorWaitingQueue(project_id=staff[0].project_id, visitor_id=visitors[0].id,
        session_id=session.id, status="waiting", expired_at=datetime.utcnow() + timedelta(minutes=5))
    db.add(entry)
    db.commit()
    return app, db, staff, visitors, session, entry, dispatch


@pytest.mark.asyncio
async def test_legacy_queue_claim_commits_once_and_rejects_repeat(queue_api):
    app, db, staff, visitors, session, entry, dispatch = queue_api
    response = await send(app, "POST", "/waiting-queue/accept", proof(staff[0]), {"visitor_id": str(visitors[0].id)})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    db.refresh(entry)
    db.refresh(session)
    assert entry.status == "assigned" and entry.assigned_staff_id == session.staff_id == staff[0].id
    repeated = await send(app, "POST", "/waiting-queue/accept", proof(staff[0]), {"visitor_id": str(visitors[0].id)})
    assert repeated.status_code in (404, 409)
    assert db.query(VisitorAssignmentHistory).count() == 1
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoked", "queue", "session", "expired"])
async def test_queue_claim_rechecks_original_wait_after_selection(queue_api, monkeypatch, change):
    app, db, staff, visitors, session, entry, dispatch = queue_api

    async def select(**_kwargs):
        if change == "revoked":
            staff[0].token_version += 1
        elif change == "queue":
            entry.reason = "new request context"
            entry.updated_at = datetime.utcnow() + timedelta(seconds=1)
        elif change == "session":
            session.status = "closed"
            session.updated_at = datetime.utcnow() + timedelta(seconds=1)
        else:
            entry.expired_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        return transfer_service.StaffAssignmentResult(assigned_staff_id=staff[0].id, candidate_staff_ids=[staff[0].id])

    monkeypatch.setattr(transfer_service, "assign_staff", AsyncMock(side_effect=select))
    response = await send(app, "POST", "/waiting-queue/accept", proof(staff[0]), {"visitor_id": str(visitors[0].id)})
    assert response.status_code == (401 if change == "revoked" else 409)
    db.refresh(entry)
    db.refresh(session)
    assert entry.status == "waiting" and session.staff_id is None
    assert db.query(VisitorAssignmentHistory).count() == 0
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoked", "customer", "session", "queue"])
async def test_queue_claim_revoke_during_dispatch_preserves_commit_but_refuses_reply(queue_api, change):
    app, db, staff, visitors, session, entry, dispatch = queue_api

    async def revoke(**_kwargs):
        if change == "revoked":
            staff[0].account_enabled = False
        elif change == "customer":
            visitors[0].updated_at = datetime.utcnow() + timedelta(seconds=1)
        elif change == "session":
            session.updated_at = datetime.utcnow() + timedelta(seconds=1)
        else:
            entry.updated_at = datetime.utcnow() + timedelta(seconds=1)
        db.commit()

    dispatch.side_effect = revoke
    response = await send(app, "POST", "/waiting-queue/accept", proof(staff[0]), {"visitor_id": str(visitors[0].id)})
    assert response.status_code == (401 if change == "revoked" else 409) and "session_id" not in response.text
    db.refresh(entry)
    assert entry.status == "assigned" and db.query(VisitorAssignmentHistory).count() == 1


@pytest.mark.asyncio
async def test_cancel_current_wait_closes_exact_session_and_no_store(queue_api):
    app, db, staff, visitors, session, entry, dispatch = queue_api
    result = await send(app, "POST", f"/waiting-queue/{entry.id}/cancel", proof(staff[0]))
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    db.refresh(entry)
    db.refresh(session)
    db.refresh(visitors[0])
    assert entry.status == "cancelled" and session.status == "closed" and visitors[0].service_status == "closed"
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["customer", "session", "queue"])
async def test_real_queue_claim_stops_notice_after_context_change(
    queue_api, monkeypatch, change,
):
    from app.models import AssignmentChannelDelivery
    from app.services import assignment_channel_delivery
    from app.services.wukongim_client import wukongim_client

    app, db, staff, visitors, session, entry, _ = queue_api

    async def change_context(**_kwargs):
        target = {
            "customer": visitors[0], "session": session, "queue": entry,
        }[change]
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
        app, "POST", "/waiting-queue/accept", proof(staff[0]),
        {"visitor_id": str(visitors[0].id)},
    )
    assert response.status_code == 409 and "session_id" not in response.text
    db.expire_all()
    row = db.query(AssignmentChannelDelivery).one()
    assert row.effects_status == "done" and row.status == "skipped"
    assert entry.status == "assigned" and session.staff_id == staff[0].id
    message.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_wait_cannot_cancel_new_customer_session(queue_api):
    app, db, staff, visitors, session, entry, _ = queue_api
    session.status = "closed"
    newer = VisitorSession(project_id=staff[0].project_id, visitor_id=visitors[0].id,
        platform_id=visitors[0].platform_id, status="open", staff_id=staff[0].id,
        created_at=datetime.utcnow() + timedelta(seconds=1))
    db.add(newer)
    db.commit()
    response = await send(app, "POST", f"/waiting-queue/{entry.id}/cancel", proof(staff[0]))
    assert response.status_code == 409
    db.refresh(entry)
    db.refresh(newer)
    assert entry.status == "waiting" and newer.status == "open"
