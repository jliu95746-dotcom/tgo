"""Assignment and channel changes must share the caller's transaction."""

import importlib
import importlib.util
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import ChannelMember, VisitorAssignmentHistory
from app.services import transfer_service
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE, MEMBER_TYPE_STAFF
from app.utils.encoding import build_visitor_channel_id
from tests import test_staff_assignment_capacity as capacity_checks

queue_db = capacity_checks.queue_db
reception = capacity_checks.reception
REAL_PREPARE_CHANNEL = transfer_service._add_staff_to_channel
pytestmark = pytest.mark.asyncio


@pytest.fixture
def channel(reception, monkeypatch):
    db, visitor, session, entry, staff, rule = reception
    ChannelMember.__table__.create(db.get_bind())
    # Keep the baseline runnable; only new source owns this new table.
    if importlib.util.find_spec("app.models.assignment_channel_delivery"):
        delivery = importlib.import_module(
            "app.models.assignment_channel_delivery"
        ).AssignmentChannelDelivery
        delivery.__table__.create(db.get_bind())
    old_member = ChannelMember(
        project_id=visitor.project_id,
        channel_id=build_visitor_channel_id(visitor.id),
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        member_id=staff[1].id,
        member_type=MEMBER_TYPE_STAFF,
    )
    db.add(old_member)
    db.commit()
    monkeypatch.setattr(
        transfer_service,
        "_add_staff_to_channel",
        REAL_PREPARE_CHANNEL,
    )
    monkeypatch.setattr(wukongim_client, "enabled", True)
    for name in (
        "add_channel_subscribers",
        "remove_channel_subscribers",
        "delete_conversation",
        "send_staff_assigned_message",
        "send_message",
    ):
        monkeypatch.setattr(wukongim_client, name, AsyncMock())
    return reception, old_member


@pytest.mark.parametrize("finish", ["rollback", "commit"])
async def test_no_im_change_before_the_assignment_owner_commits(
    channel, finish
):
    (db, visitor, session, entry, staff, _rule), old = channel
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        target_staff_id=staff[0].id,
        session_id=session.id,
        expected_queue_entry_id=entry.id,
        auto_commit=False,
        ai_disabled=True,
    )
    assert result.success, result.message
    wukongim_client.remove_channel_subscribers.assert_not_awaited()
    wukongim_client.add_channel_subscribers.assert_not_awaited()
    wukongim_client.send_staff_assigned_message.assert_not_awaited()
    active = db.query(ChannelMember).filter_by(deleted_at=None).all()
    assert [member.member_id for member in active] == [staff[0].id]
    if finish == "rollback":
        db.rollback()
        assert old.deleted_at is None
        assert session.staff_id is None and entry.status == "waiting"
        assert db.query(VisitorAssignmentHistory).count() == 0
    else:
        db.commit()
        assert session.staff_id == staff[0].id and entry.status == "assigned"
    wukongim_client.add_channel_subscribers.assert_not_awaited()


async def commit_assignment(channel, *, notify=True):
    from app.models import AssignmentChannelDelivery

    (db, visitor, session, entry, staff, _), _old = channel
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        target_staff_id=staff[0].id,
        session_id=session.id,
        expected_queue_entry_id=entry.id,
        auto_commit=False,
        ai_disabled=True,
        send_notification=notify,
    )
    assert result.success, result.message
    db.commit()
    row = db.query(AssignmentChannelDelivery).one()
    wukongim_client.send_message.return_value = SimpleNamespace(
        client_msg_no=row.client_msg_no,
    )
    return db, visitor, session, entry, staff, row


async def test_recovery_delivers_committed_membership_and_notice_once(channel):
    from app.services.assignment_channel_delivery import (
        recover_assignment_channels,
    )

    db, _, session, entry, staff, row = await commit_assignment(channel)
    assert row.effects_status == "pending" and row.status == "pending"
    assert await recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.effects_status == "done" and row.status == "sent"
    assert row.effects_attempts == 1
    assert session.staff_id == staff[0].id and entry.status == "assigned"
    assert db.query(VisitorAssignmentHistory).count() == 1
    assert await recover_assignment_channels(db) == 0
    wukongim_client.add_channel_subscribers.assert_awaited_once()
    wukongim_client.remove_channel_subscribers.assert_awaited_once()
    wukongim_client.delete_conversation.assert_awaited_once()
    wukongim_client.send_message.assert_awaited_once()
    assert (
        wukongim_client.send_message.await_args.kwargs["client_msg_no"]
        == row.client_msg_no
    )


async def test_rollback_removes_delivery_intent_with_assignment(channel):
    from app.models import AssignmentChannelDelivery
    from app.services.assignment_channel_delivery import (
        recover_assignment_channels,
    )

    (db, visitor, session, entry, staff, _), old = channel
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        target_staff_id=staff[0].id,
        session_id=session.id,
        expected_queue_entry_id=entry.id,
        auto_commit=False,
        ai_disabled=True,
    )
    assert result.success
    assert db.query(AssignmentChannelDelivery).count() == 1
    db.rollback()
    assert db.query(AssignmentChannelDelivery).count() == 0
    assert await recover_assignment_channels(db) == 0
    assert old.deleted_at is None and session.staff_id is None
    assert entry.status == "waiting"
    wukongim_client.send_message.assert_not_awaited()


async def test_disabled_im_keeps_pending_work_until_enabled(
    channel, monkeypatch
):
    from app.services.assignment_channel_delivery import (
        recover_assignment_channels,
    )

    db, _, _, _, _, row = await commit_assignment(channel)
    monkeypatch.setattr(wukongim_client, "enabled", False)
    assert await recover_assignment_channels(db) == 0
    assert row.effects_status == "pending" and row.status == "pending"
    monkeypatch.setattr(wukongim_client, "enabled", True)
    assert await recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.effects_status == "done" and row.status == "sent"


@pytest.mark.parametrize(
    "change",
    [
        "disabled",
        "paused",
        "inactive",
        "deleted",
        "agent",
        "platform",
        "retired",
        "closed",
        "reassigned",
        "new_session",
    ],
)
async def test_recovery_never_adds_outdated_assignment(channel, change):
    from app.models import Platform, VisitorSession
    from app.services.assignment_channel_delivery import (
        recover_assignment_channels,
    )

    db, visitor, session, _, staff, row = await commit_assignment(channel)
    if change in {"disabled", "paused", "inactive", "deleted", "agent"}:
        field, value = {
            "disabled": ("account_enabled", False),
            "paused": ("service_paused", True),
            "inactive": ("is_active", False),
            "deleted": ("deleted_at", datetime.now(timezone.utc)),
            "agent": ("role", "agent"),
        }[change]
        setattr(staff[0], field, value)
    elif change == "platform":
        db.get(Platform, visitor.platform_id).is_active = False
    elif change == "retired":
        visitor.access_retired_at = datetime.now(timezone.utc)
    elif change == "closed":
        session.status = "closed"
    elif change == "reassigned":
        session.staff_id = staff[1].id
    else:
        db.add(
            VisitorSession(
                project_id=visitor.project_id,
                visitor_id=visitor.id,
                platform_id=visitor.platform_id,
                staff_id=staff[0].id,
                status="closed",
                created_at=session.created_at + timedelta(seconds=1),
            )
        )
    db.commit()
    assert await recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.effects_status == "skipped" and row.status == "skipped"
    wukongim_client.add_channel_subscribers.assert_not_awaited()
    wukongim_client.send_message.assert_not_awaited()


@pytest.mark.parametrize("change", ["owner", "disabled", "platform"])
async def test_assignment_change_during_add_compensates_stale_subscriber(
    channel,
    monkeypatch,
    change,
):
    from app.models import Platform
    from app.services.assignment_channel_delivery import (
        recover_assignment_channels,
    )

    db, visitor, session, _, staff, row = await commit_assignment(channel)
    target_uid = f"{staff[0].id}-staff"

    async def add(**kwargs):
        assert kwargs["subscribers"] == [target_uid]
        if change == "owner":
            session.staff_id = staff[1].id
        elif change == "disabled":
            staff[0].account_enabled = False
        else:
            db.get(Platform, visitor.platform_id).is_active = False
        db.commit()

    monkeypatch.setattr(
        wukongim_client, "add_channel_subscribers", AsyncMock(side_effect=add)
    )
    assert await recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.effects_status == "skipped" and row.status == "skipped"
    assert wukongim_client.remove_channel_subscribers.await_args.kwargs[
        "subscribers"
    ] == [target_uid]
    wukongim_client.send_message.assert_not_awaited()


async def test_unknown_membership_retries_without_repeating_assignment(
    channel,
):
    from app.services.assignment_channel_delivery import (
        recover_assignment_channels,
    )

    db, _, _, _, _, row = await commit_assignment(channel)
    wukongim_client.add_channel_subscribers.side_effect = TimeoutError(
        "synthetic"
    )
    assert await recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.effects_status == "unknown" and row.status == "pending"
    wukongim_client.send_message.assert_not_awaited()
    assert await recover_assignment_channels(db) == 0
    row.effects_updated_at = datetime.now(timezone.utc) - timedelta(seconds=31)
    db.commit()
    wukongim_client.add_channel_subscribers.side_effect = None
    assert await recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.effects_status == "done" and row.status == "sent"
    assert row.effects_attempts == 2
    assert db.query(VisitorAssignmentHistory).count() == 1


@pytest.mark.parametrize("lookup", ["match", "missing", "foreign", "error"])
async def test_unknown_notice_queries_same_identity_without_resending(
    channel, monkeypatch, lookup
):
    from app.services.assignment_channel_delivery import (
        recover_assignment_channels,
    )

    db, _, _, _, _, row = await commit_assignment(channel)
    wukongim_client.send_message.side_effect = TimeoutError("synthetic")
    assert await recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.effects_status == "done" and row.status == "unknown"
    assert await recover_assignment_channels(db) == 0
    row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=31)
    db.commit()
    message = SimpleNamespace(
        client_msg_no=row.client_msg_no,
        channel_id=row.channel_id,
        channel_type=row.channel_type,
        from_uid=row.from_uid,
        payload=row.payload,
    )
    if lookup == "foreign":
        message.from_uid = "other"
    query = AsyncMock(
        return_value=None if lookup == "missing" else message,
        side_effect=RuntimeError("synthetic") if lookup == "error" else None,
    )
    monkeypatch.setattr(wukongim_client, "get_message_by_client_msg_no", query)
    assert await recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.status == ("sent" if lookup == "match" else "unknown")
    wukongim_client.send_message.assert_awaited_once()
    wukongim_client.add_channel_subscribers.assert_awaited_once()
    assert query.await_args.kwargs["client_msg_no"] == row.client_msg_no


async def test_no_notice_requested_still_synchronizes_channel(channel):
    from app.services.assignment_channel_delivery import (
        recover_assignment_channels,
    )

    db, _, _, _, _, row = await commit_assignment(channel, notify=False)
    assert await recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.effects_status == "done" and row.status == "skipped"
    wukongim_client.add_channel_subscribers.assert_awaited_once()
    wukongim_client.send_message.assert_not_awaited()


async def test_soft_deleted_member_is_still_removed_from_im(channel):
    from app.services.assignment_channel_delivery import (
        recover_assignment_channels,
    )

    (db, _, _, _, staff, _), old = channel
    old.deleted_at = datetime.now(timezone.utc)
    db.commit()
    await commit_assignment(channel)
    assert await recover_assignment_channels(db) == 1
    assert wukongim_client.remove_channel_subscribers.await_args.kwargs[
        "subscribers"
    ] == [f"{staff[1].id}-staff"]


@pytest.mark.parametrize("phase", ["member", "notice"])
async def test_cancellation_keeps_committed_intent_and_releases_transport_lock(
    channel, phase
):
    from app.services.assignment_channel_delivery import (
        deliver_assignment_channel,
    )
    from app.services.channel_transport_lock import channel_transport_session

    db, visitor, _, _, _, row = await commit_assignment(channel)
    call = (
        wukongim_client.add_channel_subscribers
        if phase == "member"
        else wukongim_client.send_message
    )
    call.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await deliver_assignment_channel(db, row.id)
    db.refresh(row)
    assert row.effects_status == ("unknown" if phase == "member" else "done")
    assert row.status == ("pending" if phase == "member" else "sending")
    db.rollback()
    with channel_transport_session(db, visitor.project_id, visitor.id) as work:
        assert work is not None


async def test_delivery_refuses_to_rollback_uncommitted_caller_changes(
    channel,
):
    from app.services.assignment_channel_delivery import (
        deliver_assignment_channel,
    )

    db, visitor, _, _, _, row = await commit_assignment(channel)
    operation_id = row.id
    visitor.name = "Unsaved caller edit"
    with pytest.raises(ValueError, match="Commit business changes"):
        await deliver_assignment_channel(db, operation_id)
    assert visitor.name == "Unsaved caller edit" and visitor in db.dirty
    wukongim_client.add_channel_subscribers.assert_not_awaited()


async def test_recovery_exception_has_a_cooldown(channel, monkeypatch):
    from app.services import assignment_channel_delivery as delivery

    db, _, _, _, _, row = await commit_assignment(channel)
    attempt = AsyncMock(
        side_effect=RuntimeError("synthetic recovery interruption")
    )
    monkeypatch.setattr(delivery, "deliver_assignment_channel", attempt)
    assert await delivery.recover_assignment_channels(db) == 1
    db.refresh(row)
    assert row.effects_status == "unknown"
    assert row.effects_last_error == "RECOVERY_FAILED"
    assert await delivery.recover_assignment_channels(db) == 0
    attempt.assert_awaited_once()
