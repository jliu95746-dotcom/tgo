"""Delivery acceptance and history recovery must not repeat channel delivery."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.staff_delivery import StaffDeliveryRequest
from app.services import staff_delivery
from app.services.staff_message_target import StaffMessageTarget


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    StaffMessageDelivery.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def target():
    return StaffMessageTarget(
        project_id=uuid4(),
        staff_id=uuid4(),
        visitor_id=uuid4(),
        platform_id=uuid4(),
        platform_type="custom",
        platform_api_key="isolated-test-key",
        platform_open_id="test-recipient",
        channel_id=f"{uuid4()}-vtr",
        channel_type=251,
    )


def request(target, **changes):
    return StaffDeliveryRequest(
        channel_id=target.channel_id,
        channel_type=251,
        client_msg_no="local-test-send-1",
        payload={"type": 1, "content": "你好"},
    ).model_copy(update=changes)


@pytest.mark.asyncio
async def test_history_failure_is_sent_and_duplicate_only_recovers_history(
    db, target, monkeypatch
):
    external = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    history = AsyncMock(side_effect=[RuntimeError("IM unavailable"), None])
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", history)
    first = await staff_delivery.deliver(db, target, request(target))
    assert first.delivery_status == "sent" and first.history_status == "pending"
    assert db.query(StaffMessageDelivery).count() == 1
    # A fresh session models the next request/worker after process restart.
    with Session(db.bind) as resumed:
        again = await staff_delivery.deliver(resumed, target, request(target))
    assert again.delivery_status == "sent" and again.history_status == "sent"
    assert external.await_count == 1
    assert history.await_count == 2


@pytest.mark.asyncio
async def test_timeout_stays_unknown_and_is_never_automatically_resent(
    db, target, monkeypatch
):
    external = AsyncMock(side_effect=httpx.ReadTimeout("upstream timeout"))
    history = AsyncMock()
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", history)
    first = await staff_delivery.deliver(db, target, request(target))
    again = await staff_delivery.deliver(db, target, request(target, retry_failed=True))
    assert first.delivery_status == again.delivery_status == "unknown"
    assert external.await_count == 1
    assert await staff_delivery.recover_pending_history(db) == 0
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_definite_rejection_needs_explicit_retry(db, target, monkeypatch):
    external = AsyncMock(
        side_effect=[
            httpx.Response(
                400, json={"error": {"code": "INVALID_PAYLOAD", "message": "不支持该消息类型"}}
            ),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    history = AsyncMock()
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", history)
    first = await staff_delivery.deliver(db, target, request(target))
    assert first.delivery_status == "failed" and first.error_message == "不支持该消息类型"
    same = await staff_delivery.deliver(db, target, request(target))
    assert same.delivery_status == "failed" and external.await_count == 1
    assert await staff_delivery.recover_pending_history(db) == 0
    retry = await staff_delivery.deliver(db, target, request(target, retry_failed=True))
    assert retry.delivery_status == "sent" and external.await_count == 2
    history.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_identity_with_different_payload_cannot_resend(
    db, target, monkeypatch
):
    external = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", AsyncMock())
    await staff_delivery.deliver(db, target, request(target))
    with pytest.raises(HTTPException) as raised:
        await staff_delivery.deliver(
            db, target, request(target, payload={"type": 1, "content": "不同内容"})
        )
    assert raised.value.status_code == 409
    assert external.await_count == 1


@pytest.mark.asyncio
async def test_worker_retries_only_due_accepted_history(db, target, monkeypatch):
    external = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(
        staff_delivery,
        "write_delivery_history",
        AsyncMock(side_effect=RuntimeError("offline")),
    )
    await staff_delivery.deliver(db, target, request(target))
    row = db.query(StaffMessageDelivery).one()
    row.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    history = AsyncMock()
    monkeypatch.setattr(staff_delivery, "write_delivery_history", history)
    assert await staff_delivery.recover_pending_history(db) == 1
    assert await staff_delivery.recover_pending_history(db) == 0
    assert external.await_count == 1
    history.assert_awaited_once()


@pytest.mark.asyncio
async def test_simulator_stays_internal_and_disabled_im_is_not_success(
    db, target, monkeypatch
):
    target = target.model_copy(update={"platform_open_id": target.channel_id})
    external = AsyncMock()
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(
        staff_delivery,
        "write_delivery_history",
        AsyncMock(side_effect=RuntimeError("IM disabled")),
    )
    result = await staff_delivery.deliver(db, target, request(target))
    assert result.delivery_status == "pending" and result.history_status == "pending"
    external.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_error_after_provider_acceptance_is_uncertain(
    db, target, monkeypatch
):
    external = AsyncMock(
        return_value=httpx.Response(502, json={"error": {"code": "HTTP_ERROR"}})
    )
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", AsyncMock())
    result = await staff_delivery.deliver(db, target, request(target))
    assert result.delivery_status == "unknown"
    await staff_delivery.deliver(db, target, request(target, retry_failed=True))
    assert external.await_count == 1


@pytest.mark.asyncio
async def test_identity_is_scoped_to_project_and_staff(db, target, monkeypatch):
    external = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", AsyncMock())
    await staff_delivery.deliver(db, target, request(target))
    other = target.model_copy(update={"project_id": uuid4(), "staff_id": uuid4()})
    await staff_delivery.deliver(db, other, request(other))
    assert db.query(StaffMessageDelivery).count() == 2
    assert external.await_count == 2
    unauthorized = target.model_copy(update={"staff_id": uuid4()})
    with pytest.raises(HTTPException) as raised:
        staff_delivery.get_delivery(db, unauthorized, "local-test-send-1")
    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_history_attempt_reconciles_before_resending(
    db, target, monkeypatch
):
    from types import SimpleNamespace

    row = StaffMessageDelivery(
        staff_id=target.staff_id,
        channel_id=target.channel_id,
        channel_type=251,
        client_msg_no="original-id",
        payload={"type": 1, "content": "你好"},
        history_attempts=1,
    )
    sync = AsyncMock(
        return_value=SimpleNamespace(
            client_msg_no="original-id",
            from_uid=f"{target.staff_id}-staff",
            payload=row.payload,
        )
    )
    send = AsyncMock()
    monkeypatch.setattr(staff_delivery.wukongim_client, "enabled", True)
    monkeypatch.setattr(
        staff_delivery.wukongim_client, "get_message_by_client_msg_no", sync
    )
    monkeypatch.setattr(staff_delivery.wukongim_client, "send_message", send)
    await staff_delivery.write_delivery_history(row)
    send.assert_not_awaited()
    assert sync.call_args.kwargs["channel_id"] == target.channel_id
    assert sync.call_args.kwargs["raise_on_error"] is True


@pytest.mark.asyncio
async def test_retired_personal_channel_cannot_send(
    db, target, monkeypatch
):
    target = target.model_copy(update={"platform_type": "wechat_personal"})
    external = AsyncMock()
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    with pytest.raises(HTTPException) as raised:
        await staff_delivery.deliver(db, target, request(target))
    assert raised.value.status_code == 410
    assert db.query(StaffMessageDelivery).count() == 0
    external.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_pending_receipts_is_sender_scoped_and_cursor_stable(
    db, target, monkeypatch
):
    monkeypatch.setattr(
        staff_delivery,
        "forward_staff_platform_message",
        AsyncMock(side_effect=httpx.ReadTimeout("unknown")),
    )
    for number in ["pending-a", "pending-b", "pending-c"]:
        await staff_delivery.deliver(db, target, request(target, client_msg_no=number))
    first = staff_delivery.pending_deliveries(db, target, limit=2)
    assert [item.request.client_msg_no for item in first] == ["pending-a", "pending-b"]
    assert first[0].receipt.delivery_status == "unknown"
    assert first[0].request.payload == {"type": 1, "content": "你好"}
    # A record settling between pages cannot make the cursor skip another record.
    row = staff_delivery.get_delivery(db, target, "pending-a")
    row.history_status = "sent"
    db.commit()
    second = staff_delivery.pending_deliveries(db, target, limit=2, after="pending-b")
    assert [item.request.client_msg_no for item in second] == ["pending-c"]
    for changed in [
        {"staff_id": uuid4()},
        {"project_id": uuid4()},
        {"channel_id": "other-vtr"},
    ]:
        assert (
            staff_delivery.pending_deliveries(db, target.model_copy(update=changed))
            == []
        )
