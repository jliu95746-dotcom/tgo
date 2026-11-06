"""Manual session commands must retain the original staff and exact session."""

from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles

from app.api.v1.endpoints import sessions as endpoints
from app.core.security import create_access_token
from app.models import (
    ChannelMember,
    Permission,
    RolePermission,
    VisitorAssignmentHistory,
    VisitorAssignmentRule,
    VisitorSession,
    VisitorWaitingQueue,
    SessionCommandDelivery,
)
from app.schemas.visitor_session import VisitorSessionResponse
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id
from tests.test_staff_visitor_read_access import staff_read_api  # noqa: F401
from tests.test_visitor_profile_access import profile_api  # noqa: F401
from tests.test_im_access import records  # noqa: F401


@compiles(ARRAY, "sqlite")
def compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def bearer(staff):
    return {
        "Authorization": "Bearer "
        + create_access_token(
            staff.username,
            staff.project_id,
            staff.role,
        )
    }


@pytest.fixture
def session_api(staff_read_api, monkeypatch):  # noqa: F811
    app, db, _, staff, platforms, visitors, other_staff, _ = staff_read_api
    app.include_router(endpoints.router, prefix="/sessions")
    original = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.visitor_id == visitors[0].id,
        )
        .one()
    )
    original.platform_id = platforms[0].id
    db.commit()
    close = AsyncMock(return_value=original)

    async def transfer_original(**kwargs):
        original.staff_id = kwargs["target_staff_id"]
        db.commit()
        db.refresh(original)
        return SimpleNamespace(success=True, session=original)

    transfer = AsyncMock(side_effect=transfer_original)
    monkeypatch.setattr(endpoints, "close_visitor_session", close)
    monkeypatch.setattr(endpoints, "transfer_to_staff", transfer)
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="sent")
    )
    return app, db, staff, visitors, other_staff, original, close, transfer


async def send(app, path, staff, body=None, operation_id: UUID | None = None):
    headers = bearer(staff)
    if operation_id is not None:
        headers["X-Session-Operation-ID"] = str(operation_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.post(path, headers=headers, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    ["close_by_id", "close_by_visitor", "transfer_by_id", "transfer_by_visitor"],
)
async def test_staff_without_current_update_permission_cannot_change_a_session(
    session_api,
    command,
):
    app, db, staff, visitors, other_staff, original, close, transfer = session_api
    original.staff_id = other_staff.id
    db.commit()
    path = {
        "close_by_id": f"/sessions/{original.id}/close",
        "close_by_visitor": f"/sessions/visitor/{visitors[0].id}/close",
        "transfer_by_id": f"/sessions/{original.id}/transfer",
        "transfer_by_visitor": f"/sessions/visitor/{visitors[0].id}/transfer",
    }[command]
    body = {"target_staff_id": str(staff[0].id)} if "transfer" in command else None
    result = await send(app, path, other_staff, body)
    assert result.status_code == 403
    close.assert_not_awaited()
    transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_by_session_id_passes_the_exact_original_session(session_api):
    app, db, staff, visitors, other_staff, original, close, transfer = session_api
    target = other_staff
    target.is_active = True
    db.add(Permission(resource="visitors", action="update"))
    db.flush()
    db.add(
        RolePermission(
            role="user",
            permission_id=db.query(Permission)
            .filter(
                Permission.resource == "visitors",
                Permission.action == "update",
            )
            .one()
            .id,
        )
    )
    db.commit()
    result = await send(
        app,
        f"/sessions/{original.id}/transfer",
        staff[0],
        {"target_staff_id": str(target.id)},
    )
    assert result.status_code == 200
    assert transfer.await_args.kwargs["session_id"] == original.id


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["close", "transfer"])
async def test_revoked_staff_after_call_gets_no_success_or_notification(
    session_api,
    monkeypatch,
    command,
):
    app, db, staff, _, target, original, close, transfer = session_api
    target.is_active = True
    db.commit()

    async def revoke(**_kwargs):
        staff[0].account_enabled = False
        db.commit()
        return (
            original
            if command == "close"
            else SimpleNamespace(success=True, session=original)
        )

    if command == "close":
        close.side_effect = revoke
        path = f"/sessions/{original.id}/close"
        body = None
    else:
        transfer.side_effect = revoke
        path = f"/sessions/{original.id}/transfer"
        body = {"target_staff_id": str(target.id)}
    notification = AsyncMock()
    monkeypatch.setattr(endpoints, "deliver_session_command", notification)
    result = await send(app, path, staff[0], body)
    assert result.status_code == 401
    assert "success" not in result.text.lower()
    notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_platform_after_close_does_not_expose_result(session_api):
    app, db, staff, visitors, _, original, close, _ = session_api

    async def disable_platform(**_kwargs):
        original.status = "closed"
        db.commit()
        platform = visitors[0].platform
        platform.is_active = False
        db.commit()
        return original

    close.side_effect = disable_platform
    result = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert result.status_code == 404
    assert str(original.id) not in result.text


@pytest.mark.asyncio
async def test_target_role_change_after_transfer_blocks_notification(
    session_api, monkeypatch
):
    app, db, staff, _, target, original, _, transfer = session_api
    target.is_active = True
    db.commit()

    async def change_target_role(**_kwargs):
        original.staff_id = target.id
        target.role = "agent"
        db.commit()
        db.refresh(original)
        return SimpleNamespace(success=True, session=original)

    transfer.side_effect = change_target_role
    notification = AsyncMock()
    monkeypatch.setattr(endpoints, "deliver_session_command", notification)
    result = await send(
        app,
        f"/sessions/{original.id}/transfer",
        staff[0],
        {"target_staff_id": str(target.id)},
    )
    assert result.status_code == 409
    notification.assert_not_awaited()


@pytest.fixture
def real_session_api(session_api, monkeypatch):
    from app.services import session_command_delivery, session_service, transfer_service

    app, db, staff, visitors, target, original, _, _ = session_api
    for model in (
        VisitorAssignmentRule,
        VisitorAssignmentHistory,
        VisitorWaitingQueue,
        SessionCommandDelivery,
    ):
        model.__table__.create(db.get_bind(), checkfirst=True)
    monkeypatch.setattr(
        endpoints, "close_visitor_session", session_service.close_visitor_session
    )
    monkeypatch.setattr(
        endpoints, "transfer_to_staff", transfer_service.transfer_to_staff
    )
    monkeypatch.setattr(
        endpoints,
        "deliver_session_command",
        session_command_delivery.deliver_session_command,
    )
    monkeypatch.setattr(
        endpoints, "deliver_channel_effects", AsyncMock(return_value="pending")
    )
    monkeypatch.setattr(session_command_delivery.wukongim_client, "enabled", True)
    monkeypatch.setattr(
        session_command_delivery.wukongim_client,
        "send_message",
        AsyncMock(return_value=SimpleNamespace(message_id="fixture-message")),
    )
    monkeypatch.setattr(transfer_service, "_add_staff_to_channel", AsyncMock(return_value=None))
    monkeypatch.setattr(
        session_service.wukongim_client,
        "get_channel_last_message",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        session_service.wukongim_client, "remove_channel_subscribers", AsyncMock()
    )
    monkeypatch.setattr(
        session_service.wukongim_client, "send_session_closed_message", AsyncMock()
    )
    monkeypatch.setattr(
        session_service.wukongim_client, "delete_conversation", AsyncMock()
    )
    monkeypatch.setattr(session_service, "trigger_queue_for_staff", AsyncMock())
    return app, db, staff, visitors, target, original


@pytest.mark.asyncio
async def test_real_close_commits_only_the_original_session(real_session_api):
    app, db, staff, _, _, original = real_session_api
    result = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert result.status_code == 200
    assert result.json()["id"] == str(original.id)
    assert result.json()["status"] == "closed"
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-session-notification-status"] == "sent"
    operation_id = result.headers["x-session-operation-id"]
    delivery = db.get(SessionCommandDelivery, UUID(operation_id))
    assert delivery is not None
    assert delivery.session_id == original.id and delivery.status == "sent"
    db.refresh(original)
    assert original.status == "closed"


@pytest.mark.asyncio
async def test_close_commits_channel_cleanup_intent_with_session(
    real_session_api, monkeypatch,
):
    app, db, staff, visitors, _, original = real_session_api
    channel_id = build_visitor_channel_id(visitors[0].id)
    member = ChannelMember(
        project_id=staff[0].project_id,
        channel_id=channel_id,
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        member_id=staff[0].id,
        member_type="staff",
    )
    db.add(member)
    db.commit()
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )

    response = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert response.status_code == 200
    delivery = db.get(
        SessionCommandDelivery, UUID(response.headers["x-session-operation-id"])
    )
    db.refresh(member)
    assert member.deleted_at is not None
    assert delivery is not None and delivery.effects_status == "pending"
    assert delivery.effects == [
        {"kind": "remove_subscriber", "staff_id": str(staff[0].id)},
        {"kind": "delete_conversation", "staff_id": str(staff[0].id)},
    ]


@pytest.mark.asyncio
async def test_transfer_commits_member_switch_and_effects(
    real_session_api, monkeypatch,
):
    app, db, staff, visitors, target, original = real_session_api
    channel_id = build_visitor_channel_id(visitors[0].id)
    old_member = ChannelMember(
        project_id=staff[0].project_id,
        channel_id=channel_id,
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        member_id=staff[0].id,
        member_type="staff",
    )
    db.add(old_member)
    db.commit()
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )

    response = await send(
        app, f"/sessions/{original.id}/transfer", staff[0],
        {"target_staff_id": str(target.id)},
    )
    assert response.status_code == 200
    delivery = db.get(
        SessionCommandDelivery, UUID(response.headers["x-session-operation-id"])
    )
    db.refresh(old_member)
    target_member = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.member_id == target.id,
        ChannelMember.deleted_at.is_(None),
    ).one_or_none()
    assert old_member.deleted_at is not None
    assert target_member is not None
    assert delivery is not None and delivery.effects_status == "pending"
    assert delivery.effects == [
        {"kind": "remove_subscriber", "staff_id": str(staff[0].id)},
        {"kind": "delete_conversation", "staff_id": str(staff[0].id)},
        {"kind": "add_subscriber", "staff_id": str(target.id)},
    ]


@pytest.mark.asyncio
async def test_committed_close_channel_effects_recover_after_request(
    real_session_api, monkeypatch,
):
    from app.services import session_command_membership

    app, db, staff, _, _, original = real_session_api
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )
    result = await send(app, f"/sessions/{original.id}/close", staff[0])
    operation_id = UUID(result.headers["x-session-operation-id"])
    remove = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "remove_channel_subscribers", remove,
    )
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "delete_conversation", delete,
    )

    assert await session_command_membership.deliver_channel_effects(
        db, operation_id
    ) == "done"
    assert await session_command_membership.deliver_channel_effects(
        db, operation_id
    ) == "done"
    remove.assert_awaited_once()
    delete.assert_awaited_once()
    delivery = db.get(SessionCommandDelivery, operation_id)
    assert delivery is not None and delivery.effects_attempts == 1


@pytest.mark.asyncio
async def test_old_close_effects_do_not_remove_current_staff(
    real_session_api, monkeypatch,
):
    from app.services import session_command_membership

    app, db, staff, visitors, _, original = real_session_api
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )
    result = await send(app, f"/sessions/{original.id}/close", staff[0])
    operation_id = UUID(result.headers["x-session-operation-id"])
    db.add(VisitorSession(
        project_id=staff[0].project_id,
        visitor_id=visitors[0].id,
        platform_id=visitors[0].platform_id,
        staff_id=staff[0].id,
        status="open",
        created_at=original.created_at + timedelta(seconds=1),
    ))
    db.commit()
    remove = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "remove_channel_subscribers", remove,
    )
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "delete_conversation", delete,
    )

    assert await session_command_membership.deliver_channel_effects(
        db, operation_id
    ) == "skipped"
    remove.assert_not_awaited()
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_channel_effects_replace_subscriber(
    real_session_api, monkeypatch,
):
    from app.services import session_command_membership

    app, db, staff, _, target, original = real_session_api
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )
    result = await send(
        app, f"/sessions/{original.id}/transfer", staff[0],
        {"target_staff_id": str(target.id)},
    )
    operation_id = UUID(result.headers["x-session-operation-id"])
    remove = AsyncMock()
    delete = AsyncMock()
    add = AsyncMock()
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "remove_channel_subscribers", remove,
    )
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "delete_conversation", delete,
    )
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "add_channel_subscribers", add,
    )

    assert await session_command_membership.deliver_channel_effects(
        db, operation_id
    ) == "done"
    remove.assert_awaited_once()
    delete.assert_awaited_once()
    add.assert_awaited_once()
    assert add.await_args.kwargs["subscribers"] == [f"{target.id}-staff"]


@pytest.mark.asyncio
async def test_disabled_platform_cannot_add_transfer_subscriber(
    real_session_api, monkeypatch,
):
    from app.services import session_command_membership

    app, db, staff, visitors, target, original = real_session_api
    monkeypatch.setattr(
        endpoints, "deliver_session_command",
        AsyncMock(return_value="pending"),
    )
    response = await send(
        app, f"/sessions/{original.id}/transfer", staff[0],
        {"target_staff_id": str(target.id)},
    )
    assert response.status_code == 200
    operation_id = UUID(response.headers["x-session-operation-id"])
    visitors[0].platform.is_active = False
    db.commit()
    remove = AsyncMock()
    delete = AsyncMock()
    add = AsyncMock()
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "remove_channel_subscribers", remove,
    )
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "delete_conversation", delete,
    )
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "add_channel_subscribers", add,
    )

    assert await session_command_membership.deliver_channel_effects(
        db, operation_id,
    ) == "skipped"
    delivery = db.get(SessionCommandDelivery, operation_id)
    assert delivery is not None
    assert delivery.effects_last_error == "TARGET_NO_LONGER_AVAILABLE"
    remove.assert_awaited_once()
    delete.assert_awaited_once()
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_reports_inline_channel_effects_in_receipt(
    real_session_api, monkeypatch,
):
    from app.services import session_command_membership

    app, db, staff, _, _, original = real_session_api
    monkeypatch.setattr(
        endpoints, "deliver_channel_effects",
        session_command_membership.deliver_channel_effects,
    )
    result = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert result.status_code == 200
    assert result.headers["x-session-effects-status"] == "done"
    operation_id = result.headers["x-session-operation-id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        receipt = await client.get(
            f"/sessions/operations/{operation_id}", headers=bearer(staff[0])
        )
    assert receipt.status_code == 200
    assert receipt.json()["effects_status"] == "done"
    assert receipt.json()["effects_attempts"] == 1
    assert db.get(SessionCommandDelivery, UUID(operation_id)) is not None


@pytest.mark.asyncio
async def test_uncertain_channel_effect_retries_without_new_session_command(
    real_session_api, monkeypatch,
):
    from app.services import session_command_membership

    app, db, staff, _, _, original = real_session_api
    result = await send(app, f"/sessions/{original.id}/close", staff[0])
    operation_id = UUID(result.headers["x-session-operation-id"])
    remove = AsyncMock(side_effect=[TimeoutError("unknown"), None])
    delete = AsyncMock()
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "remove_channel_subscribers", remove,
    )
    monkeypatch.setattr(
        session_command_membership.wukongim_client,
        "delete_conversation", delete,
    )
    assert await session_command_membership.deliver_channel_effects(
        db, operation_id
    ) == "unknown"
    assert await session_command_membership.deliver_channel_effects(
        db, operation_id
    ) == "done"
    assert remove.await_count == 2
    delete.assert_awaited_once()
    assert db.query(SessionCommandDelivery).count() == 1


@pytest.mark.asyncio
async def test_channel_effect_recovery_failure_enters_cooldown(
    real_session_api, monkeypatch,
):
    from app.services import session_command_membership

    app, db, staff, _, _, original = real_session_api
    result = await send(app, f"/sessions/{original.id}/close", staff[0])
    operation_id = UUID(result.headers["x-session-operation-id"])
    attempt = AsyncMock(side_effect=RuntimeError("temporary"))
    monkeypatch.setattr(session_command_membership, "deliver_channel_effects", attempt)

    assert await session_command_membership.recover_channel_effects(db, limit=1) == 0
    delivery = db.get(SessionCommandDelivery, operation_id)
    assert delivery is not None and delivery.effects_status == "unknown"
    assert delivery.effects_last_error == "RECOVERY_FAILED"
    assert await session_command_membership.recover_channel_effects(db, limit=1) == 0
    attempt.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_operation_id_recovers_committed_close_without_repeating_it(
    real_session_api, monkeypatch
):
    from app.services import session_command_delivery

    app, db, staff, visitors, _, original = real_session_api
    operation_id = uuid4()
    send_message = AsyncMock(return_value=SimpleNamespace(message_id="one-message"))
    monkeypatch.setattr(
        session_command_delivery.wukongim_client, "send_message", send_message
    )

    first = await send(
        app, f"/sessions/{original.id}/close", staff[0], operation_id=operation_id
    )
    assert first.status_code == 200
    assert first.headers["x-session-operation-id"] == str(operation_id)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        receipt = await client.get(
            f"/sessions/operations/{operation_id}", headers=bearer(staff[0])
        )
    assert receipt.status_code == 200
    assert receipt.json()["visitor_id"] == str(visitors[0].id)
    assert receipt.json()["kind"] == "close"
    assert receipt.json()["to_staff_id"] is None

    duplicate = await send(
        app, f"/sessions/{original.id}/close", staff[0], operation_id=operation_id
    )
    assert duplicate.status_code == 409
    assert db.query(SessionCommandDelivery).count() == 1
    send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_operation_id_can_be_reused_after_uncommitted_rejection(
    real_session_api,
):
    app, db, staff, _, _, original = real_session_api
    operation_id = uuid4()
    staff[0].account_enabled = False
    db.commit()
    rejected = await send(
        app, f"/sessions/{original.id}/close", staff[0], operation_id=operation_id
    )
    assert rejected.status_code == 401
    assert db.get(SessionCommandDelivery, operation_id) is None

    staff[0].account_enabled = True
    db.commit()
    accepted = await send(
        app, f"/sessions/{original.id}/close", staff[0], operation_id=operation_id
    )
    assert accepted.status_code == 200
    assert accepted.headers["x-session-operation-id"] == str(operation_id)


@pytest.mark.asyncio
async def test_committed_close_unknown_receipt_is_reconciled_without_resending(
    real_session_api, monkeypatch
):
    from app.services import session_command_delivery

    app, db, staff, _, _, original = real_session_api
    send_message = AsyncMock(side_effect=TimeoutError("no receipt"))
    monkeypatch.setattr(
        session_command_delivery.wukongim_client, "send_message", send_message
    )
    response = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert response.status_code == 200
    assert response.headers["x-session-notification-status"] == "unknown"
    operation_id = response.headers["x-session-operation-id"]
    delivery = db.get(SessionCommandDelivery, UUID(operation_id))
    assert delivery is not None and delivery.attempts == 1

    lookup = AsyncMock(
        return_value=SimpleNamespace(
            from_uid=delivery.from_uid,
            payload=delivery.payload,
            message_id="accepted-original",
        )
    )
    monkeypatch.setattr(
        session_command_delivery.wukongim_client,
        "get_message_by_client_msg_no",
        lookup,
    )
    status = await session_command_delivery.deliver_session_command(db, delivery.id)
    assert status == "sent"
    db.refresh(delivery)
    assert delivery.message_id == "accepted-original"
    send_message.assert_awaited_once()
    assert lookup.await_args.kwargs["client_msg_no"] == delivery.client_msg_no


@pytest.mark.asyncio
async def test_unknown_notice_without_message_stays_unknown_and_is_not_resent(
    real_session_api, monkeypatch
):
    from app.services import session_command_delivery

    app, db, staff, _, _, original = real_session_api
    send_message = AsyncMock(side_effect=TimeoutError("receipt lost"))
    monkeypatch.setattr(
        session_command_delivery.wukongim_client, "send_message", send_message
    )
    response = await send(app, f"/sessions/{original.id}/close", staff[0])
    operation_id = UUID(response.headers["x-session-operation-id"])
    monkeypatch.setattr(
        session_command_delivery.wukongim_client,
        "get_message_by_client_msg_no",
        AsyncMock(return_value=None),
    )
    assert (
        await session_command_delivery.deliver_session_command(db, operation_id)
        == "unknown"
    )
    delivery = db.get(SessionCommandDelivery, operation_id)
    assert delivery is not None and delivery.attempts == 1
    send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_close_notice_recovers_after_request_is_gone(
    real_session_api, monkeypatch
):
    from app.services import session_command_delivery

    app, db, staff, _, _, original = real_session_api
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )
    response = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert response.status_code == 200
    operation_id = response.headers["x-session-operation-id"]
    delivery = db.get(SessionCommandDelivery, UUID(operation_id))
    assert delivery is not None and delivery.status == "pending"
    assert await session_command_delivery.recover_session_commands(db) == 1
    db.refresh(delivery)
    assert delivery.status == "sent"
    assert await session_command_delivery.recover_session_commands(db) == 0


@pytest.mark.asyncio
async def test_session_command_recovery_continues_after_one_row_fails(
    monkeypatch,
):
    from app.services import session_command_delivery

    first_id, second_id = uuid4(), uuid4()
    db = MagicMock()
    rows = (
        db.query.return_value.filter.return_value.order_by.return_value.limit
    )
    rows.return_value.all.return_value = [
        (first_id,), (second_id,)
    ]
    monkeypatch.setattr(
        session_command_delivery.wukongim_client, "enabled", True
    )
    deliver = AsyncMock(side_effect=[RuntimeError("first failed"), "sent"])
    monkeypatch.setattr(
        session_command_delivery, "deliver_session_command", deliver
    )

    assert await session_command_delivery.recover_session_commands(db) == 1
    assert [call.args[1] for call in deliver.await_args_list] == [
        first_id, second_id,
    ]
    assert db.rollback.call_count >= 2


@pytest.mark.asyncio
async def test_failed_pending_notice_waits_before_recovery_retry(
    real_session_api, monkeypatch,
):
    from app.services import session_command_delivery

    app, db, staff, _, _, original = real_session_api
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )
    response = await send(app, f"/sessions/{original.id}/close", staff[0])
    operation_id = UUID(response.headers["x-session-operation-id"])
    delivery = db.get(SessionCommandDelivery, operation_id)
    assert delivery is not None and delivery.status == "pending"

    deliver = AsyncMock(side_effect=[RuntimeError("temporary"), "sent"])
    monkeypatch.setattr(
        session_command_delivery, "deliver_session_command", deliver
    )
    assert await session_command_delivery.recover_session_commands(db) == 0
    db.refresh(delivery)
    assert delivery.last_error == "RECOVERY_FAILED"
    assert await session_command_delivery.recover_session_commands(db) == 0
    deliver.assert_awaited_once()

    delivery.updated_at = datetime.now(timezone.utc) - timedelta(seconds=31)
    db.commit()
    assert await session_command_delivery.recover_session_commands(db) == 1
    assert deliver.await_count == 2


@pytest.mark.asyncio
async def test_failed_oldest_notice_does_not_starve_later_work(
    real_session_api, monkeypatch,
):
    from app.services import session_command_delivery

    app, db, staff, _, _, original = real_session_api
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )
    response = await send(app, f"/sessions/{original.id}/close", staff[0])
    oldest_id = UUID(response.headers["x-session-operation-id"])
    oldest = db.get(SessionCommandDelivery, oldest_id)
    assert oldest is not None

    copied = {
        column.key: getattr(oldest, column.key)
        for column in SessionCommandDelivery.__mapper__.column_attrs
    }
    later_id = uuid4()
    copied.update(
        id=later_id,
        client_msg_no=f"session-command-{later_id.hex}",
        created_at=oldest.created_at + timedelta(seconds=1),
        updated_at=oldest.updated_at + timedelta(seconds=1),
    )
    db.add(SessionCommandDelivery(**copied))
    db.commit()

    deliver = AsyncMock(side_effect=[RuntimeError("temporary"), "sent"])
    monkeypatch.setattr(
        session_command_delivery, "deliver_session_command", deliver
    )
    assert (
        await session_command_delivery.recover_session_commands(db, limit=1)
        == 0
    )
    assert (
        await session_command_delivery.recover_session_commands(db, limit=1)
        == 1
    )
    assert [call.args[1] for call in deliver.await_args_list] == [
        oldest_id, later_id,
    ]


@pytest.mark.asyncio
async def test_revoked_actor_cannot_publish_pending_close_notice(
    real_session_api, monkeypatch
):
    from app.services import session_command_delivery

    app, db, staff, _, _, original = real_session_api
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )
    response = await send(app, f"/sessions/{original.id}/close", staff[0])
    operation_id = UUID(response.headers["x-session-operation-id"])
    staff[0].account_enabled = False
    db.commit()
    send_message = AsyncMock()
    monkeypatch.setattr(
        session_command_delivery.wukongim_client, "send_message", send_message
    )
    assert await session_command_delivery.recover_session_commands(db) == 1
    delivery = db.get(SessionCommandDelivery, operation_id)
    assert delivery is not None and delivery.status == "skipped"
    send_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["close", "transfer"])
async def test_superseded_session_cannot_publish_pending_notice(
    real_session_api, monkeypatch, command,
):
    from app.services import session_command_delivery

    app, db, staff, visitors, target, original = real_session_api
    target.is_active = True
    db.commit()
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )
    path = f"/sessions/{original.id}/{command}"
    body = (
        {"target_staff_id": str(target.id)}
        if command == "transfer" else None
    )
    response = await send(app, path, staff[0], body)
    assert response.status_code == 200
    operation_id = UUID(response.headers["x-session-operation-id"])

    newer = VisitorSession(
        project_id=staff[0].project_id,
        visitor_id=visitors[0].id,
        platform_id=visitors[0].platform_id,
        staff_id=target.id,
        status="open",
        created_at=original.created_at + timedelta(minutes=1),
    )
    db.add(newer)
    db.commit()
    send_message = AsyncMock()
    monkeypatch.setattr(
        session_command_delivery.wukongim_client, "send_message", send_message,
    )

    assert await session_command_delivery.deliver_session_command(
        db, operation_id,
    ) == "skipped"
    assert db.get(SessionCommandDelivery, operation_id).status == "skipped"
    send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_original_token_cannot_publish_pending_close_notice(
    real_session_api, monkeypatch
):
    from app.services import session_command_delivery

    app, db, staff, _, _, original = real_session_api
    monkeypatch.setattr(
        endpoints, "deliver_session_command", AsyncMock(return_value="pending")
    )
    response = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert response.status_code == 200
    operation_id = UUID(response.headers["x-session-operation-id"])
    delivery = db.get(SessionCommandDelivery, operation_id)
    assert delivery is not None
    delivery.actor_token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    send_message = AsyncMock()
    monkeypatch.setattr(
        session_command_delivery.wukongim_client, "send_message", send_message
    )
    assert await session_command_delivery.recover_session_commands(db) == 1
    db.refresh(delivery)
    assert delivery.status == "skipped"
    send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_operation_receipt_is_scoped_to_the_original_project(real_session_api):
    app, _, staff, _, _, original = real_session_api
    response = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert response.status_code == 200
    operation_id = response.headers["x-session-operation-id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        own = await client.get(
            f"/sessions/operations/{operation_id}", headers=bearer(staff[0])
        )
        foreign = await client.get(
            f"/sessions/operations/{operation_id}", headers=bearer(staff[1])
        )
    assert own.status_code == 200
    assert own.json()["notification_status"] == "sent"
    assert "payload" not in own.text
    assert own.headers["cache-control"] == "no-store"
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_close_by_visitor_changes_only_the_latest_open_session(real_session_api):
    app, db, staff, visitors, _, original = real_session_api
    latest = VisitorSession(
        project_id=staff[0].project_id,
        visitor_id=visitors[0].id,
        platform_id=visitors[0].platform_id,
        staff_id=staff[0].id,
        status="open",
        created_at=original.created_at + timedelta(seconds=1),
    )
    db.add(latest)
    db.commit()

    result = await send(app, f"/sessions/visitor/{visitors[0].id}/close", staff[0])
    assert result.status_code == 200
    assert result.json()["id"] == str(latest.id)
    db.refresh(original)
    db.refresh(latest)
    assert original.status == "open"
    assert latest.status == "closed"


@pytest.mark.asyncio
async def test_close_revocation_while_serializing_hides_the_committed_result(
    real_session_api,
    monkeypatch,
):
    app, db, staff, _, _, original = real_session_api
    validate = VisitorSessionResponse.model_validate

    def revoke(value, *args, **kwargs):
        result = validate(value, *args, **kwargs)
        staff[0].account_enabled = False
        db.commit()
        return result

    monkeypatch.setattr(VisitorSessionResponse, "model_validate", revoke)
    response = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert response.status_code == 401
    assert str(original.id) not in response.text
    db.refresh(original)
    assert original.status == "closed"


@pytest.mark.asyncio
async def test_close_rechecks_revocation_after_last_message_wait(
    real_session_api, monkeypatch
):
    from app.services import session_service

    app, db, staff, _, _, original = real_session_api

    async def revoke(**_kwargs):
        staff[0].account_enabled = False
        db.commit()
        return None

    monkeypatch.setattr(
        session_service.wukongim_client,
        "get_channel_last_message",
        AsyncMock(side_effect=revoke),
    )
    result = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert result.status_code == 401
    db.refresh(original)
    assert original.status == "open"


@pytest.mark.asyncio
async def test_real_transfer_keeps_original_session_and_exact_target(real_session_api):
    app, db, staff, _, target, original = real_session_api
    result = await send(
        app,
        f"/sessions/{original.id}/transfer",
        staff[0],
        {"target_staff_id": str(target.id)},
    )
    assert result.status_code == 200
    assert result.json()["old_session_id"] == str(original.id)
    assert result.json()["new_session_id"] == str(original.id)
    assert result.headers["cache-control"] == "no-store"
    db.refresh(original)
    assert original.status == "open" and original.staff_id == target.id


@pytest.mark.asyncio
async def test_transfer_receipt_identifies_the_original_target(real_session_api):
    app, db, staff, visitors, target, original = real_session_api
    operation_id = uuid4()
    response = await send(
        app,
        f"/sessions/{original.id}/transfer",
        staff[0],
        {"target_staff_id": str(target.id)},
        operation_id=operation_id,
    )
    assert response.status_code == 200
    assert response.headers["x-session-operation-id"] == str(operation_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        receipt = await client.get(
            f"/sessions/operations/{operation_id}", headers=bearer(staff[0])
        )
    assert receipt.status_code == 200
    assert receipt.json()["visitor_id"] == str(visitors[0].id)
    assert receipt.json()["to_staff_id"] == str(target.id)
    assert db.query(SessionCommandDelivery).count() == 1


@pytest.mark.asyncio
async def test_transfer_by_visitor_changes_only_the_latest_open_session(
    real_session_api,
):
    app, db, staff, visitors, target, original = real_session_api
    latest = VisitorSession(
        project_id=staff[0].project_id,
        visitor_id=visitors[0].id,
        platform_id=visitors[0].platform_id,
        staff_id=staff[0].id,
        status="open",
        created_at=original.created_at + timedelta(seconds=1),
    )
    db.add(latest)
    db.commit()

    result = await send(
        app,
        f"/sessions/visitor/{visitors[0].id}/transfer",
        staff[0],
        {"target_staff_id": str(target.id)},
    )
    assert result.status_code == 200
    assert result.json()["old_session_id"] == str(latest.id)
    assert result.json()["new_session_id"] == str(latest.id)
    db.refresh(original)
    db.refresh(latest)
    assert original.staff_id == staff[0].id
    assert latest.staff_id == target.id


@pytest.mark.asyncio
async def test_close_wait_cannot_close_a_replaced_session(
    real_session_api, monkeypatch
):
    from app.services import session_service

    app, db, staff, visitors, _, original = real_session_api

    async def replace(**_kwargs):
        newer = VisitorSession(
            project_id=staff[0].project_id,
            visitor_id=visitors[0].id,
            platform_id=visitors[0].platform_id,
            staff_id=staff[0].id,
            status="open",
            created_at=original.created_at + timedelta(seconds=1),
        )
        db.add(newer)
        db.commit()
        return None

    monkeypatch.setattr(
        session_service.wukongim_client,
        "get_channel_last_message",
        AsyncMock(side_effect=replace),
    )
    result = await send(app, f"/sessions/{original.id}/close", staff[0])
    assert result.status_code == 409
    db.refresh(original)
    assert original.status == "open"


@pytest.mark.asyncio
async def test_transfer_revocation_during_assignment_does_not_commit_or_notify(
    real_session_api, monkeypatch
):
    from app.services import transfer_service

    app, db, staff, _, target, original = real_session_api

    async def revoke(**_kwargs):
        staff[0].account_enabled = False
        db.commit()
        return transfer_service.StaffAssignmentResult(
            assigned_staff_id=target.id,
            candidate_staff_ids=[target.id],
        )

    monkeypatch.setattr(transfer_service, "assign_staff", AsyncMock(side_effect=revoke))
    notification = AsyncMock()
    monkeypatch.setattr(endpoints, "deliver_session_command", notification)
    result = await send(
        app,
        f"/sessions/{original.id}/transfer",
        staff[0],
        {"target_staff_id": str(target.id)},
    )
    assert result.status_code == 401
    db.refresh(original)
    assert original.staff_id == staff[0].id
    assert db.query(VisitorAssignmentHistory).count() == 0
    notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_target_does_not_fall_back_to_another_staff(real_session_api):
    app, db, staff, _, target, original = real_session_api
    target.account_enabled = False
    db.commit()
    result = await send(
        app,
        f"/sessions/{original.id}/transfer",
        staff[0],
        {"target_staff_id": str(target.id)},
    )
    assert result.status_code in (400, 409)
    db.refresh(original)
    assert original.staff_id == staff[0].id
    assert db.query(VisitorAssignmentHistory).count() == 0


@pytest.mark.asyncio
async def test_duplicate_transfer_to_current_owner_does_not_create_history(
    real_session_api,
):
    app, db, staff, _, _, original = real_session_api
    result = await send(
        app,
        f"/sessions/{original.id}/transfer",
        staff[0],
        {"target_staff_id": str(staff[0].id)},
    )
    assert result.status_code in (400, 409)
    assert db.query(VisitorAssignmentHistory).count() == 0


@pytest.mark.asyncio
async def test_current_owner_is_required_even_when_role_has_update_permission(
    session_api,
):
    app, db, staff, visitors, other_staff, original, close, transfer = session_api
    permission = Permission(resource="visitors", action="update")
    db.add(permission)
    db.flush()
    db.add(RolePermission(role="user", permission_id=permission.id))
    db.commit()
    result = await send(app, f"/sessions/{original.id}/close", other_staff)
    assert result.status_code == 404
    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_project_session_cannot_be_closed(session_api):
    app, db, staff, visitors, _, _, close, _ = session_api
    foreign = VisitorSession(
        project_id=staff[1].project_id,
        visitor_id=visitors[1].id,
        staff_id=staff[1].id,
        status="open",
    )
    db.add(foreign)
    db.commit()
    result = await send(app, f"/sessions/{foreign.id}/close", staff[0])
    assert result.status_code == 404
    close.assert_not_awaited()
