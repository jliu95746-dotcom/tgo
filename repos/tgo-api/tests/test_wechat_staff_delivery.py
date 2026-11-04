"""A transferred human reply keeps its original transport and real receipt."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import Column, MetaData, Table, Uuid, create_engine
from sqlalchemy.orm import Session

from app.models.staff_message_delivery import StaffMessageDelivery, utc_now
from app.models.visitor_session import VisitorSession
from app.models.wechat_staff_delivery import WeChatStaffDelivery
from app.schemas.staff_delivery import StaffDeliveryRequest
from app.services import staff_delivery, wechat_staff_delivery as service
from app.services.staff_message_target import StaffMessageTarget


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite://")
    visitors = Table("api_visitors", MetaData(), Column("id", Uuid, primary_key=True))
    visitors.create(engine)
    VisitorSession.__table__.create(engine)
    StaffMessageDelivery.__table__.create(engine)
    WeChatStaffDelivery.__table__.create(engine)
    with Session(engine) as db:
        project, platform, visitor_id, staff = (uuid4() for _ in range(4))
        db.execute(visitors.insert().values(id=visitor_id))
        visitor = SimpleNamespace(
            id=visitor_id,
            project_id=project,
            platform_id=platform,
            platform_open_id="wechat-local:original-account:private:customer",
            service_mode="auto",
            ai_disabled=False,
        )
        owner = SimpleNamespace(id=uuid4(), staff_id=staff)
        current = service.Ownership(visitor, owner.id, owner.staff_id, uuid4())
        monkeypatch.setattr(service, "lock_context", lambda *_: current)
        monkeypatch.setattr(service, "require_human_service", lambda *_: None)
        target = StaffMessageTarget(
            project_id=project,
            platform_id=platform,
            visitor_id=visitor_id,
            staff_id=staff,
            platform_type="wechat_local",
            platform_api_key="fixture-key",
            platform_open_id=visitor.platform_open_id,
            channel_id=f"{visitor_id}-vtr",
            channel_type=251,
        )
        req = StaffDeliveryRequest(
            channel_id=target.channel_id,
            client_msg_no="human-one",
            payload={"type": 1, "content": "由接手员工回复"},
        )
        yield db, target, req, current
    engine.dispose()


def test_enqueued_reply_stays_pending_and_keeps_original_route(context):
    db, target, req, owner = context
    row = service.enqueue(db, target, req)
    queue = db.get(WeChatStaffDelivery, row.id)
    assert staff_delivery.receipt(row).delivery_status == "pending"
    assert staff_delivery.receipt(row).delivery_transport == "wechat_local"
    assert row.history_status == "pending"
    assert queue.platform_id == target.platform_id
    assert queue.platform_open_id == target.platform_open_id
    assert queue.owner_staff_id == target.staff_id
    assert owner.visitor.service_mode == "manual" and owner.visitor.ai_disabled
    assert service.enqueue(db, target, req).id == row.id
    assert db.query(WeChatStaffDelivery).count() == 1


def test_claim_is_idempotent_but_another_worker_cannot_send(context):
    db, target, req, owner = context
    row = service.enqueue(db, target, req)
    attempt = uuid4()
    claim = service.claim(db, row.id, target.platform_id, owner.visitor, attempt)
    assert (
        service.claim(db, row.id, target.platform_id, owner.visitor, attempt) == claim
    )
    with pytest.raises(HTTPException) as error:
        service.claim(db, row.id, target.platform_id, owner.visitor, uuid4())
    assert error.value.status_code == 409
    assert (
        staff_delivery.receipt(db.get(StaffMessageDelivery, row.id)).delivery_status
        == "processing"
    )


def test_transfer_before_claim_cancels_old_owner_reply(context, monkeypatch):
    db, target, req, owner = context
    row = service.enqueue(db, target, req)
    changed = service.Ownership(owner.visitor, owner.session_id, uuid4(), uuid4())
    monkeypatch.setattr(service, "lock_context", lambda *_: changed)
    with pytest.raises(HTTPException) as error:
        service.claim(db, row.id, target.platform_id, owner.visitor, uuid4())
    assert error.value.status_code == 409
    assert db.get(WeChatStaffDelivery, row.id).state == "cancelled"
    assert db.get(StaffMessageDelivery, row.id).external_status == "failed"


@pytest.mark.asyncio
async def test_actual_receipt_publishes_once_and_late_receipt_recovers_unknown(
    context, monkeypatch
):
    db, target, req, owner = context
    row = service.enqueue(db, target, req)
    claim = service.claim(db, row.id, target.platform_id, owner.visitor, uuid4())
    queue = db.get(WeChatStaffDelivery, row.id)
    queue.claimed_at = utc_now() - timedelta(seconds=121)
    db.commit()
    with pytest.raises(HTTPException):
        service.check(db, row.id, target.platform_id, owner.visitor, None)
    assert queue.state == "unknown"
    history = AsyncMock()

    async def write(row):
        await history(row.id)

    monkeypatch.setattr(staff_delivery, "write_delivery_history", write)
    await service.confirm(
        db, row.id, target.platform_id, owner.visitor, claim, "sent", "a" * 64
    )
    await service.confirm(
        db, row.id, target.platform_id, owner.visitor, claim, "sent", "a" * 64
    )
    assert staff_delivery.receipt(row).delivery_status == "sent"
    history.assert_awaited_once()
    assert row.payload["extra"]["wechat_local_delivery_id"] == str(row.id)


def test_wrong_original_channel_cannot_claim_or_mutate(context):
    db, target, req, owner = context
    row = service.enqueue(db, target, req)
    with pytest.raises(HTTPException) as error:
        service.claim(db, row.id, uuid4(), owner.visitor, uuid4())
    assert error.value.status_code == 403
    assert db.get(WeChatStaffDelivery, row.id).state == "queued"


def test_unknown_first_send_blocks_later_replies_and_is_not_reclaimed(context):
    db, target, req, owner = context
    first = service.enqueue(db, target, req)
    second = service.enqueue(
        db, target, req.model_copy(update={"client_msg_no": "human-two"})
    )
    attempt = uuid4()
    service.claim(db, first.id, target.platform_id, owner.visitor, attempt)
    queue = db.get(WeChatStaffDelivery, first.id)
    queue.claimed_at = utc_now() - timedelta(seconds=121)
    db.commit()
    assert service.ready(db, target.platform_id, owner.visitor) == []
    with pytest.raises(HTTPException):
        service.claim(db, first.id, target.platform_id, owner.visitor, attempt)
    with pytest.raises(HTTPException):
        service.claim(db, second.id, target.platform_id, owner.visitor, uuid4())


def test_completed_human_history_is_not_a_send_instruction_to_old_bridge():
    from app.schemas.wukongim import WuKongIMChannelMessageSyncResponse, WuKongIMMessage
    from app.services.wechat_staff_delivery import mask_bridge_history

    message = WuKongIMMessage(
        header={"no_persist": 0, "red_dot": 0, "sync_once": 0},
        setting=0,
        message_id=123,
        client_msg_no="original",
        message_seq=42,
        from_uid="employee-staff",
        channel_id="visitor-vtr",
        channel_type=251,
        timestamp=100,
        payload={
            "type": 1,
            "content": "已经实际发出",
            "extra": {"wechat_local_delivery_id": str(uuid4())},
        },
    )
    result = WuKongIMChannelMessageSyncResponse(
        start_message_seq=42, end_message_seq=42, more=0, messages=[message]
    )
    masked = mask_bridge_history(result)
    assert masked.messages[0].message_seq == 42
    assert masked.messages[0].payload["type"] != 1
    assert not masked.messages[0].payload.get("content")
    assert message.payload["content"] == "已经实际发出"  # Staff history stays intact.


def test_different_client_payload_cannot_replace_accepted_reply(context):
    db, target, req, owner = context
    first = service.enqueue(db, target, req)
    with pytest.raises(HTTPException) as error:
        service.enqueue(
            db, target, req.model_copy(update={"payload": {"type": 1, "content": "修改"}})
        )
    assert error.value.status_code == 409
    assert db.get(WeChatStaffDelivery, first.id).text == "由接手员工回复"
