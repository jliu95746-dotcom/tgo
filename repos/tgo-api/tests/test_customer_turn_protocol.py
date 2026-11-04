"""Transport retries and worker restarts never publish a stale customer turn."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.schemas.customer_turn import TurnReceipt, TurnSuperseded
from app.services import customer_turn_ingress, customer_turn_worker
from app.services.customer_turn_store import TurnStore, utc
from tests.test_customer_turn_store import args, message
from tests.test_customer_turn_store import turns as _turns_fixture

turns = _turns_fixture


def test_retry_transport_flags_do_not_make_another_turn(turns):
    scope = args()
    first, _ = turns.ingest(**scope, request=message(stream=True))
    repeated, _ = turns.ingest(
        **scope, request=message(stream=False, wukongim_only=True)
    )
    assert (
        repeated.duplicate
        and repeated.turn_id == first.turn_id
        and repeated.version == 1
    )


def test_supplement_during_generation_starts_a_new_bounded_collection_window(turns):
    scope = args()
    now = datetime.now(timezone.utc)
    first, _ = turns.ingest(**scope, request=message(), now=now)
    turn = turns.get(first.turn_id)
    turn.state = "processing"
    newer, _ = turns.ingest(
        **scope, request=message("new", "改为蓝色"), now=now + timedelta(seconds=20)
    )
    assert utc(turns.get(newer.turn_id).due_at) == now + timedelta(seconds=22)
    assert [part.payload["message"] for part in turns.parts(newer.turn_id)] == [
        "你们的GEO",
        "改为蓝色",
    ]


def test_same_sender_in_two_channel_conversations_does_not_merge(turns):
    scope = args()
    one, _ = turns.ingest(**scope, request=message(), conversation="a" * 64)
    two, _ = turns.ingest(**scope, request=message("second"), conversation="b" * 64)
    assert one.turn_id != two.turn_id


def test_expired_processing_lease_recovers_with_a_new_version(turns, monkeypatch):
    scope = args()
    first, _ = turns.ingest(**scope, request=message())
    row = turns.get(first.turn_id)
    row.state = "processing"
    row.lease_owner, row.lease_until = uuid4(), datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    turns.db.commit()
    monkeypatch.setattr(
        customer_turn_worker, "SessionLocal", lambda: Session(turns.db.get_bind())
    )
    recovered = customer_turn_worker.recover()
    assert recovered[0][0] == first.turn_id
    turns.db.expire_all()
    assert turns.get(first.turn_id).version == 2
    with pytest.raises(TurnSuperseded):
        turns.stage(first.turn_id, 1, "过期答案", "staff-staff", None)


def test_exact_delivery_receipt_survives_database_reopen(turns):
    first, _ = turns.ingest(**args(), request=message())
    turns.stage(first.turn_id, 1, "完整答复", "staff-staff", None)
    claim = turns.claim(first.turn_id, 1).claim_id
    turns.confirm(first.turn_id, 1, claim, "sent", "provider:actual-receipt")
    turns.db.commit()
    with Session(turns.db.get_bind()) as reopened:
        row = TurnStore(reopened).confirm(
            first.turn_id, 1, claim, "sent", "provider:actual-receipt"
        )
        assert row.state == "sent" and row.reply_text == "完整答复"


def test_ambiguous_send_never_retries_but_a_late_actual_receipt_can_settle(turns):
    scope = args()
    first, _ = turns.ingest(**scope, request=message())
    turns.stage(first.turn_id, 1, "完整答复", "staff-staff", None)
    claim = turns.claim(first.turn_id, 1).claim_id
    turns.confirm(first.turn_id, 1, claim, "unknown", None)
    turns.get(first.turn_id).failure = "发送结果待核对，停止自动重试"
    with pytest.raises(HTTPException):
        turns.claim(first.turn_id, 1)
    with pytest.raises(HTTPException):
        turns.ingest(**scope, request=message("next", "补充"))
    assert (
        turns.confirm(first.turn_id, 1, claim, "sent", "actual-provider-receipt").state
        == "sent"
    )
    assert turns.get(first.turn_id).failure is None
    second, _ = turns.ingest(**scope, request=message("next", "补充"))
    assert second.turn_id != first.turn_id


def test_send_timeout_starts_at_claim_instead_of_old_reply_ready_time(
    turns, monkeypatch
):
    first, _ = turns.ingest(**args(), request=message())
    turns.stage(first.turn_id, 1, "完整答复", "staff-staff", None)
    row = turns.get(first.turn_id)
    row.ready_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    turns.claim(first.turn_id, 1)
    turns.db.commit()
    monkeypatch.setattr(
        customer_turn_worker, "SessionLocal", lambda: Session(turns.db.get_bind())
    )
    customer_turn_worker.recover()
    turns.db.expire_all()
    assert turns.get(first.turn_id).state == "sending"


def test_superseded_generation_refunds_before_final_delivery_and_settles_once(
    turns, monkeypatch
):
    from app.services import ai_usage
    from unittest.mock import Mock

    release, begin, settle = Mock(), Mock(), Mock()
    monkeypatch.setattr(ai_usage, "release", release)
    monkeypatch.setattr(ai_usage, "begin_publication", begin)
    monkeypatch.setattr(ai_usage, "settle", settle)
    scope = args()
    first, _ = turns.ingest(**scope, request=message())
    permit = {"reservation_id": str(uuid4()), "lease_id": str(uuid4())}
    turns.stage(first.turn_id, 1, "旧答复", "staff-staff", permit)
    updated, _ = turns.ingest(**scope, request=message("two", "新的条件"))
    release.assert_called_once()
    settle.assert_not_called()
    turns.stage(updated.turn_id, 2, "新答复", "staff-staff", permit)
    claim = turns.claim(updated.turn_id, 2).claim_id
    begin.assert_called_once()
    settle.assert_not_called()
    turns.confirm(updated.turn_id, 2, claim, "sent", "provider-receipt")
    turns.confirm(updated.turn_id, 2, claim, "sent", "provider-receipt")
    settle.assert_called_once()


@pytest.mark.asyncio
async def test_human_takeover_cancels_all_unsent_turns_immediately(turns, monkeypatch):
    from unittest.mock import AsyncMock

    scope = args()
    first, _ = turns.ingest(**scope, request=message())
    turns.stage(first.turn_id, 1, "不能发送", "staff-staff", None)
    stop = AsyncMock()
    monkeypatch.setattr(customer_turn_ingress, "stop_run", stop)
    await customer_turn_ingress.cancel_customer_turns(
        turns.db, SimpleNamespace(id=scope["visitor"], project_id=scope["project"])
    )
    assert turns.get(first.turn_id).state == "cancelled"
    with pytest.raises(HTTPException):
        turns.claim(first.turn_id, 1)
    stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_sse_publishes_one_complete_chunk_with_the_final_version(monkeypatch):
    identity = uuid4()
    admitted = TurnReceipt(
        turn_id=identity,
        version=3,
        client_msg_no="ai-final",
        source_message_id="part-3",
    )
    payload = {
        "event_type": "workflow_completed",
        "message": "先回答商品，再回答条件。",
        "turn_id": str(identity),
        "version": 3,
        "client_msg_no": "ai-final",
        "channel_id": "visitor-vtr",
        "channel_type": 251,
        "delivery_required": True,
    }
    monkeypatch.setattr(customer_turn_ingress, "snapshot", lambda _: payload)
    response = await customer_turn_ingress.response(
        message(stream=True), admitted, SimpleNamespace(id=uuid4()), can_reply=True
    )
    frames = [frame async for frame in response.body_iterator]
    assert len(frames) == 3
    assert sum(frame.startswith("event: agent_content_chunk") for frame in frames) == 1
    final = json.loads(frames[-1].split("data: ", 1)[1])["data"]["data"]
    assert final["version"] == 3 and final["final_content"] == payload["message"]


@pytest.mark.asyncio
async def test_superseded_waiter_never_receives_old_answer(monkeypatch):
    admitted = TurnReceipt(
        turn_id=uuid4(), version=1, client_msg_no="old", source_message_id="first"
    )
    monkeypatch.setattr(
        customer_turn_ingress,
        "snapshot",
        lambda _: {"event_type": "superseded", "success": True},
    )
    response = await customer_turn_ingress.response(
        message(stream=True), admitted, SimpleNamespace(id=uuid4()), can_reply=True
    )
    frames = [frame async for frame in response.body_iterator]
    assert (
        len(frames) == 1
        and "superseded" in frames[0]
        and "final_content" not in frames[0]
    )
