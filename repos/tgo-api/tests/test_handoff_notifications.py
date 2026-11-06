"""Committed human reception notices survive sender and receipt failures."""

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import ManualServiceRequest, Project, Staff
from app.services import human_handoff_service
from app.services.wukongim_client import wukongim_client
from app.utils.const import MessageType
from app.utils.encoding import build_visitor_channel_id
from tests import test_handoff_dispatch_recovery as dispatch_checks

queue_db = dispatch_checks.queue_db
reception = dispatch_checks.reception
handoff = dispatch_checks.handoff
pytestmark = pytest.mark.asyncio


def notices():
    return importlib.import_module("app.services.handoff_notifications")


@pytest.fixture
def queued(handoff, monkeypatch):
    db, visitor, session, entry, *_ = handoff
    visitor.service_status = "queued"
    visitor.service_mode = "manual"
    visitor.ai_disabled = True
    session.status = "open"
    entry.status = "waiting"
    entry.expired_at = datetime.utcnow() + timedelta(minutes=5)
    channel = build_visitor_channel_id(visitor.id)
    request = ManualServiceRequest(
        id=uuid4(), project_id=visitor.project_id, visitor_id=visitor.id,
        reason="Synthetic handoff", channel_id=channel, channel_type=251,
        status="pending", request_metadata={},
    )
    db.add(request)
    db.commit()
    receipt = {
        "request_id": str(request.id), "entry_id": str(entry.id),
        "status": "waiting",
    }
    monkeypatch.setattr(wukongim_client, "enabled", True)
    monkeypatch.setattr(
        wukongim_client, "get_message_by_client_msg_no", AsyncMock()
    )
    return db, visitor, request, receipt, session, entry


def stage(queued):
    db, visitor, request, receipt, *_ = queued
    module = notices()
    request.request_metadata = {
        "handoff_receipt": receipt, "notification_status": "unknown",
    }
    module.stage_handoff_notice(
        request, visitor, receipt, content="已收到人工客服请求。",
        channel_id=request.channel_id, channel_type=251,
    )
    db.commit()
    return request.request_metadata[module.NOTICE_KEY]


async def test_finish_saves_notice_before_sending(queued, monkeypatch):
    db, visitor, request, receipt, *_ = queued
    request_id = request.id

    async def send(**_kwargs):
        assert db.in_transaction() is False
        with Session(db.get_bind()) as other:
            persisted = other.get(ManualServiceRequest, request_id)
            state = persisted.request_metadata.get("handoff_notification")
            assert isinstance(state, dict), "Notification intent was not saved"
            assert state["state"] == "sending" and state["attempts"] == 1

    monkeypatch.setattr(human_handoff_service, "_notify_handoff", send)
    result = await human_handoff_service._finish_handoff(
        db=db, request=request, visitor=visitor, receipt=receipt,
        action_digest="synthetic-digest", is_duplicate=False,
        content="已收到人工客服请求。", channel_id=request.channel_id,
        channel_type=251,
    )
    assert result["notification_status"] == "submitted"
    assert request.request_metadata["handoff_notification"]["state"] == "sent"


async def test_sender_claims_once_for_repeated_delivery(queued):
    db, _visitor, request, *_ = queued
    stage(queued)
    sender = human_handoff_service._notify_handoff
    assert await notices().deliver_handoff_notice(db, request.id) == "sent"
    assert await notices().deliver_handoff_notice(db, request.id) == "sent"
    sender.assert_awaited_once()
    assert sender.await_args.kwargs["client_msg_no"] == f"handoff-{request.id}"


async def test_disabled_sender_leaves_pending_work_for_recovery(
    queued, monkeypatch,
):
    db, _visitor, request, *_ = queued
    stage(queued)
    monkeypatch.setattr(wukongim_client, "enabled", False)
    assert await notices().deliver_handoff_notice(db, request.id) == "pending"
    human_handoff_service._notify_handoff.assert_not_awaited()
    monkeypatch.setattr(wukongim_client, "enabled", True)
    outcome = await notices().recover_handoff_notices(db)
    assert outcome.sent == 1
    assert request.request_metadata["notification_status"] == "submitted"
    human_handoff_service._notify_handoff.assert_awaited_once()


def history(queued, **changes):
    _db, _visitor, request, *_ = queued
    notice = request.request_metadata[notices().NOTICE_KEY]
    values = dict(
        client_msg_no=f"handoff-{request.id}", channel_id=request.channel_id,
        channel_type=251, from_uid="system", message_id=123,
        payload={"type": MessageType.HUMAN_HANDOFF_REQUESTED,
                 "content": notice["content"]},
    )
    values.update(changes)
    return SimpleNamespace(**values)


async def unknown(queued, monkeypatch):
    db, _visitor, request, *_ = queued
    stage(queued)
    sender = AsyncMock(side_effect=RuntimeError("synthetic-private-error"))
    monkeypatch.setattr(human_handoff_service, "_notify_handoff", sender)
    assert await notices().deliver_handoff_notice(db, request.id) == "unknown"
    raw = dict(request.request_metadata[notices().NOTICE_KEY])
    raw["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    request.request_metadata = {
        **request.request_metadata, notices().NOTICE_KEY: raw,
    }
    db.commit()
    return sender


@pytest.mark.parametrize(
    "change", [None, "sender", "channel", "channel_type", "number", "body"],
)
async def test_uncertain_sends_require_matching_upstream_history(
    queued, monkeypatch, change,
):
    db, _visitor, request, *_ = queued
    sender = await unknown(queued, monkeypatch)
    alterations = {
        "sender": {"from_uid": "different"},
        "channel": {"channel_id": "different"},
        "channel_type": {"channel_type": 2},
        "number": {"client_msg_no": "different"},
        "body": {"payload": {"content": "different"}},
    }
    message = history(queued, **alterations.get(change, {}))
    wukongim_client.get_message_by_client_msg_no.return_value = message
    result = await notices().recover_handoff_notices(db)
    assert result.sent == (1 if change is None else 0)
    assert request.request_metadata["notification_status"] == (
        "submitted" if change is None else "unknown"
    )
    sender.assert_awaited_once()
    assert wukongim_client.get_message_by_client_msg_no.await_args.kwargs[
        "raise_on_error"
    ] is True


@pytest.mark.parametrize("lookup_error", [False, True])
async def test_missing_or_failed_lookup_never_replays_the_send(
    queued, monkeypatch, lookup_error, caplog,
):
    db, _visitor, request, *_ = queued
    sender = await unknown(queued, monkeypatch)
    if lookup_error:
        error = RuntimeError("synthetic-private-lookup-error")
        wukongim_client.get_message_by_client_msg_no.side_effect = error
    else:
        wukongim_client.get_message_by_client_msg_no.return_value = None
    result = await notices().recover_handoff_notices(db)
    assert result.sent == 0 and result.unknown == 1
    assert request.request_metadata["notification_status"] == "unknown"
    sender.assert_awaited_once()
    assert "synthetic-private" not in caplog.text


@pytest.mark.parametrize(
    "change", ["resume", "assist", "retire", "queue", "session", "project"],
)
async def test_obsolete_pending_notices_are_skipped(
    queued, change,
):
    db, visitor, request, _receipt, session, entry = queued
    stage(queued)
    if change == "resume":
        visitor.ai_disabled = False
    elif change == "assist":
        visitor.service_mode = "assist"
    elif change == "retire":
        visitor.access_retired_at = datetime.utcnow()
    elif change == "queue":
        entry.status = "cancelled"
    elif change == "session":
        session.status = "closed"
    else:
        db.get(Project, visitor.project_id).deleted_at = datetime.utcnow()
    db.commit()
    assert await notices().deliver_handoff_notice(db, request.id) == "skipped"
    human_handoff_service._notify_handoff.assert_not_awaited()


async def test_profile_refresh_failure_cannot_suppress_handoff_notice(
    queued, monkeypatch,
):
    db, visitor, request, receipt, *_ = queued
    monkeypatch.setattr(
        human_handoff_service, "notify_visitor_profile_updated",
        AsyncMock(side_effect=RuntimeError("synthetic-private-profile-error")),
    )
    result = await human_handoff_service._finish_handoff(
        db=db, request=request, visitor=visitor, receipt=receipt,
        action_digest=None, is_duplicate=False, content="已收到人工客服请求。",
        channel_id=request.channel_id, channel_type=251,
    )
    assert result["notification_status"] == "submitted"
    human_handoff_service._notify_handoff.assert_awaited_once()
    assert request.request_metadata["handoff_receipt"] == receipt


@pytest.mark.parametrize("legacy", [True, False])
async def test_old_or_unknown_notice_records_are_not_replayed(queued, legacy):
    db, _visitor, request, *_ = queued
    request.request_metadata = {"notification_status": "unknown"}
    if not legacy:
        request.request_metadata = {
            **request.request_metadata,
            "handoff_notification": {"version": 2, "state": "pending"},
        }
    db.commit()
    result = await notices().recover_handoff_notices(db)
    assert result.checked == 0
    human_handoff_service._notify_handoff.assert_not_awaited()


async def test_accepted_notice_intent_is_immutable(queued):
    db, visitor, request, receipt, *_ = queued
    original = stage(queued)
    notices().stage_handoff_notice(
        request, visitor, receipt, content="Different later content",
        channel_id="different", channel_type=2,
    )
    db.commit()
    assert request.request_metadata[notices().NOTICE_KEY] == original


async def test_lost_local_receipt_is_reconciled_without_second_send(
    queued, monkeypatch,
):
    db, _visitor, request, *_ = queued
    request_id = request.id
    stage(queued)
    original = db.commit
    commits = 0

    def fail_receipt_commit():
        nonlocal commits
        commits += 1
        if commits == 2:
            raise RuntimeError("synthetic receipt commit failure")
        original()

    with monkeypatch.context() as scope:
        scope.setattr(db, "commit", fail_receipt_commit)
        with pytest.raises(RuntimeError, match="synthetic receipt"):
            await notices().deliver_handoff_notice(db, request_id)
    db.rollback()
    raw = dict(request.request_metadata[notices().NOTICE_KEY])
    assert raw["state"] == "sending"
    raw["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    request.request_metadata = {
        **request.request_metadata, notices().NOTICE_KEY: raw,
    }
    db.commit()
    wukongim_client.get_message_by_client_msg_no.return_value = history(queued)
    assert (await notices().recover_handoff_notices(db)).sent == 1
    human_handoff_service._notify_handoff.assert_awaited_once()


async def test_retired_access_stops_lookup_of_an_uncertain_notice(
    queued, monkeypatch,
):
    db, visitor, _request, *_ = queued
    sender = await unknown(queued, monkeypatch)
    visitor.access_retired_at = datetime.utcnow()
    db.commit()
    result = await notices().recover_handoff_notices(db)
    assert result.skipped == 1
    wukongim_client.get_message_by_client_msg_no.assert_not_awaited()
    sender.assert_awaited_once()


async def test_background_loop_visits_notices_with_its_own_cursor(monkeypatch):
    import asyncio
    from unittest.mock import MagicMock, Mock
    from app.tasks import recover_staff_delivery_history as task
    from app.services.handoff_dispatch_recovery import HandoffRecoverySweep

    monkeypatch.setattr(task, "SessionLocal", Mock(return_value=MagicMock()))
    for name in (
        "recover_stale_external_deliveries", "recover_pending_history",
        "recover_pending_training", "recover_session_commands",
        "recover_channel_effects",
    ):
        monkeypatch.setattr(task, name, AsyncMock())
    monkeypatch.setattr(task, "recover_pending_handoffs", AsyncMock(
        return_value=HandoffRecoverySweep(),
    ))
    cursor = uuid4()
    recover = AsyncMock(return_value=notices().HandoffNoticeSweep(
        next_cursor=cursor,
    ))
    monkeypatch.setattr(task, "recover_handoff_notices", recover)
    monkeypatch.setattr(task.asyncio, "sleep", AsyncMock(
        side_effect=[None, asyncio.CancelledError],
    ))
    with pytest.raises(asyncio.CancelledError):
        await task._recover_loop()
    assert recover.await_count == 2
    assert recover.await_args_list[0].kwargs["after_request_id"] is None
    assert recover.await_args_list[1].kwargs["after_request_id"] == cursor


@pytest.mark.parametrize("enabled", [True, False])
async def test_assigned_notice_requires_a_current_staff_account(
    queued, enabled,
):
    db, visitor, request, receipt, session, _entry = queued
    staff = db.query(Staff).first()
    staff.account_enabled = enabled
    session.staff_id = staff.id
    visitor.service_status = "active"
    request.status = "in_progress"
    receipt.update({
        "status": "active", "session_id": str(session.id),
        "assigned_staff_id": str(staff.id),
    })
    stage(queued)
    result = await notices().deliver_handoff_notice(db, request.id)
    assert result == ("sent" if enabled else "skipped")
    assert human_handoff_service._notify_handoff.await_count == int(enabled)
