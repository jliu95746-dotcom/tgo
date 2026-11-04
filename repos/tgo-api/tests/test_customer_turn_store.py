from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.models.customer_turn import CustomerTurn, CustomerTurnPart
from app.schemas.chat import ChatCompletionRequest
from app.schemas.customer_turn import TurnSuperseded
from app.services.customer_turn_store import TurnStore


@compiles(JSONB, "sqlite")
def jsonb(*_args, **_kwargs):
    return "JSON"


@pytest.fixture
def turns():
    engine = create_engine("sqlite://")
    CustomerTurn.__table__.create(engine)
    CustomerTurnPart.__table__.create(engine)
    with Session(engine) as db:
        yield TurnStore(db)
    engine.dispose()


def args():
    return dict(
        project=uuid4(),
        platform=uuid4(),
        visitor=uuid4(),
        channel="visitor-vtr",
        sender="customer",
        can_reply=True,
        external=True,
    )


def message(source="one", content="你们的GEO", **kwargs):
    return ChatCompletionRequest(
        api_key="fixture",
        from_uid="fixture",
        message=content,
        source_message_id=source,
        **kwargs
    )


def test_parts_keep_order_and_quiet_window_resets_but_is_capped(turns):
    scope = args()
    now = datetime.now(timezone.utc)
    first, _ = turns.ingest(**scope, request=message(), now=now)
    second, _ = turns.ingest(
        **scope, request=message("two", "怎么做关键词优化"), now=now + timedelta(seconds=1)
    )
    assert first.turn_id == second.turn_id and second.version == 2
    assert [p.payload["message"] for p in turns.parts(first.turn_id)] == [
        "你们的GEO",
        "怎么做关键词优化",
    ]
    third, _ = turns.ingest(
        **scope, request=message("three", "餐饮行业"), now=now + timedelta(seconds=4)
    )
    assert turns.get(third.turn_id).due_at.replace(
        tzinfo=timezone.utc
    ) == now + timedelta(seconds=5)


def test_duplicate_and_conflicting_source_ids(turns):
    scope = args()
    first, _ = turns.ingest(**scope, request=message())
    duplicate, _ = turns.ingest(**scope, request=message())
    assert duplicate.duplicate and duplicate.version == 1
    with pytest.raises(HTTPException):
        turns.ingest(**scope, request=message(content="changed"))
    assert len(turns.parts(first.turn_id)) == 1


def test_new_parts_fence_prepared_reply_and_do_not_merge_different_senders(turns):
    scope = args()
    first, _ = turns.ingest(**scope, request=message())
    turns.stage(first.turn_id, 1, "旧回答", "staff-staff", None)
    updated, _ = turns.ingest(**scope, request=message("two", "补充条件"))
    with pytest.raises(HTTPException):
        turns.claim(first.turn_id, 1)
    with pytest.raises(TurnSuperseded):
        turns.stage(first.turn_id, 1, "旧回答", "staff-staff", None)
    other = {**scope, "sender": "someone_else"}
    separate, _ = turns.ingest(**other, request=message("other"))
    assert separate.turn_id != updated.turn_id


def test_send_claim_is_irrevocable_and_receipts_are_idempotent(turns):
    scope = args()
    first, _ = turns.ingest(**scope, request=message())
    turns.stage(first.turn_id, 1, "新回答", "staff-staff", None)
    claim = turns.claim(first.turn_id, 1).claim_id
    following, _ = turns.ingest(**scope, request=message("two", "还有一件事"))
    assert following.turn_id != first.turn_id
    assert (
        turns.confirm(first.turn_id, 1, claim, "sent", "actual-receipt").state == "sent"
    )
    assert (
        turns.confirm(first.turn_id, 1, claim, "sent", "actual-receipt").state == "sent"
    )
    with pytest.raises(HTTPException):
        turns.confirm(first.turn_id, 1, claim, "sent", "different")


def test_media_placeholder_can_only_be_completed_in_its_owned_turn(turns):
    scope = args()
    first, _ = turns.ingest(
        **scope, request=message(content="", msg_type=2, media_pending=True)
    )
    ready, _ = turns.ingest(
        **scope,
        request=message(
            content="/v1/chat/files/fixture", msg_type=2, media_file_id=uuid4()
        )
    )
    assert (
        ready.turn_id == first.turn_id
        and not turns.parts(first.turn_id)[0].media_pending
    )
    with pytest.raises(HTTPException):
        turns.ingest(**scope, request=message(content="replace", msg_type=2))
