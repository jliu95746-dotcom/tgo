"""Contract tests for persistent human handoff behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models import (
    ManualServiceRequest,
    Staff,
    Tag,
    VisitorSession,
    VisitorTag,
    VisitorWaitingQueue,
)
from app.services import human_handoff_service
from app.utils.manual_service_tag import MANUAL_SERVICE_TAG_ID, manual_service_tag_id


def test_manual_service_request_uses_application_timestamp_defaults() -> None:
    """The legacy table has no database timestamp defaults."""

    table = ManualServiceRequest.__table__

    assert table.c.created_at.default is not None
    assert table.c.updated_at.default is not None


class _FakeQuery:
    def __init__(self, result: object) -> None:
        self._result = result

    def filter(self, *_args: object, **_kwargs: object) -> _FakeQuery:
        return self

    def order_by(self, *_args: object, **_kwargs: object) -> _FakeQuery:
        return self

    def first(self) -> object:
        return self._result


class _FakeDB:
    def __init__(self, results: dict[object, object]) -> None:
        self.results = results
        self.added: list[object] = []
        self.commits = 0

    def query(self, model: object) -> _FakeQuery:
        return _FakeQuery(self.results.get(model))

    def add(self, value: object) -> None:
        self.added.append(value)

    def flush(self) -> None:
        for value in self.added:
            if isinstance(value, ManualServiceRequest) and value.id is None:
                value.id = uuid4()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None


def test_first_handoff_creates_manual_service_tag() -> None:
    visitor = SimpleNamespace(id=uuid4())
    db = _FakeDB({Tag: None, VisitorTag: None})
    project_id = uuid4()

    human_handoff_service.ensure_manual_service_tag(db, project_id, visitor)

    tag = next(value for value in db.added if isinstance(value, Tag))
    visitor_tag = next(value for value in db.added if isinstance(value, VisitorTag))
    assert tag.id == manual_service_tag_id(project_id)
    assert visitor_tag.tag_id == tag.id


def test_existing_legacy_manual_tag_is_reused() -> None:
    project_id = uuid4()
    tag = SimpleNamespace(id=MANUAL_SERVICE_TAG_ID, deleted_at=None)
    visitor = SimpleNamespace(id=uuid4())
    db = _FakeDB({Tag: tag, VisitorTag: None})

    human_handoff_service.ensure_manual_service_tag(db, project_id, visitor)

    visitor_tag = next(value for value in db.added if isinstance(value, VisitorTag))
    assert visitor_tag.tag_id == MANUAL_SERVICE_TAG_ID


@pytest.mark.asyncio
async def test_existing_staff_handoff_is_persisted_and_notified() -> None:
    project_id = uuid4()
    visitor_id = uuid4()
    staff_id = uuid4()
    session_id = uuid4()
    visitor = SimpleNamespace(
        id=visitor_id,
        project_id=project_id,
        platform_id=uuid4(),
        service_status="active",
        is_unassigned=False,
        ai_disabled=None,
        service_mode="auto",
    )
    project = SimpleNamespace(id=project_id)
    tag = SimpleNamespace(id=MANUAL_SERVICE_TAG_ID, deleted_at=None)
    visitor_tag = SimpleNamespace(deleted_at=None)
    session = SimpleNamespace(id=session_id, staff_id=staff_id)
    staff = SimpleNamespace(name="张客服", nickname=None, username="staff")
    db = _FakeDB(
        {
            Tag: tag,
            VisitorTag: visitor_tag,
            ManualServiceRequest: None,
            VisitorWaitingQueue: None,
            VisitorSession: session,
            Staff: staff,
        }
    )

    with (
        patch.object(
            human_handoff_service,
            "notify_visitor_profile_updated",
            new=AsyncMock(),
        ) as notify_profile,
        patch.object(
            human_handoff_service,
            "_notify_handoff",
            new=AsyncMock(),
        ) as notify_handoff,
    ):
        result = await human_handoff_service.request_human_handoff(
            db=db,
            project=project,
            visitor=visitor,
            reason="我要投诉，转人工",
            source_message_id="msg-1",
            routing_reason="explicit_complaint",
            channel="wecom",
        )

    persisted = next(
        value for value in db.added if isinstance(value, ManualServiceRequest)
    )
    assert persisted.status == "in_progress"
    assert persisted.source_message_id == "msg-1"
    assert persisted.routing_reason == "explicit_complaint"
    assert persisted.request_metadata["source_message_id"] == "msg-1"
    assert visitor.ai_disabled is True
    assert visitor.service_mode == "manual"
    assert result["assigned_staff_id"] == str(staff_id)
    assert db.commits == 1
    notify_profile.assert_awaited_once_with(db, visitor)
    notify_handoff.assert_awaited_once()


@pytest.mark.asyncio
async def test_ai_handoff_keeps_committed_receipt_when_notification_fails() -> None:
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    visitor = SimpleNamespace(
        id=visitor_id, project_id=project_id, platform_id=uuid4(),
        service_status="active", is_unassigned=False, ai_disabled=None,
        service_mode="auto",
    )
    project = SimpleNamespace(id=project_id)
    db = _FakeDB({
        Tag: SimpleNamespace(id=MANUAL_SERVICE_TAG_ID, deleted_at=None),
        VisitorTag: SimpleNamespace(deleted_at=None),
        ManualServiceRequest: None,
        VisitorWaitingQueue: None,
        VisitorSession: SimpleNamespace(id=uuid4(), staff_id=staff_id),
        Staff: SimpleNamespace(name="张客服", nickname=None, username="staff"),
    })
    source_id = f"ai:{uuid4()}"
    with (
        patch.object(human_handoff_service, "notify_visitor_profile_updated",
                     new=AsyncMock()),
        patch.object(human_handoff_service, "_notify_handoff",
                     new=AsyncMock(side_effect=RuntimeError("notification unavailable"))) as notify,
    ):
        first = await human_handoff_service.request_human_handoff(
            db=db, project=project, visitor=visitor, reason="转人工",
            source_message_id=source_id, channel="wecom",
            strict_source_receipt=True,
        )
        saved = next(
            row for row in db.added if isinstance(row, ManualServiceRequest)
        )
        db.results[ManualServiceRequest] = saved
        repeated = await human_handoff_service.request_human_handoff(
            db=db, project=project, visitor=visitor, reason="转人工",
            source_message_id=source_id, channel="wecom",
            strict_source_receipt=True,
        )
        with pytest.raises(HTTPException) as changed:
            await human_handoff_service.request_human_handoff(
                db=db, project=project, visitor=visitor, reason="改为退款",
                source_message_id=source_id, channel="wecom",
                strict_source_receipt=True,
            )
    assert first["request_id"] == str(saved.id)
    assert first["notification_status"] == "unknown"
    assert repeated == first
    assert changed.value.status_code == 409
    assert saved.reason == "转人工"
    assert db.commits >= 1
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_handoff_disabled_im_keeps_notification_unconfirmed() -> None:
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    db = _FakeDB({
        Tag: SimpleNamespace(id=MANUAL_SERVICE_TAG_ID, deleted_at=None),
        VisitorTag: SimpleNamespace(deleted_at=None),
        ManualServiceRequest: None,
        VisitorWaitingQueue: None,
        VisitorSession: SimpleNamespace(id=uuid4(), staff_id=staff_id),
        Staff: SimpleNamespace(name="张客服", nickname=None, username="staff"),
    })
    visitor = SimpleNamespace(
        id=visitor_id, project_id=project_id, service_status="active",
        is_unassigned=False, ai_disabled=False, service_mode="auto",
    )
    with (
        patch.object(human_handoff_service, "notify_visitor_profile_updated",
                     new=AsyncMock()),
        patch.object(human_handoff_service.wukongim_client,
                     "send_system_message", new=AsyncMock(return_value=None)) as send,
    ):
        receipt = await human_handoff_service.request_human_handoff(
            db=db, project=SimpleNamespace(id=project_id), visitor=visitor,
            reason="转人工", source_message_id="wecom-message-1",
            channel="wecom", strict_source_receipt=True,
        )
    assert receipt["notification_status"] == "unknown"
    request = next(row for row in db.added if isinstance(row, ManualServiceRequest))
    assert request.request_metadata["notification_status"] == "unknown"
    assert send.await_args.kwargs["client_msg_no"] == f"handoff-{request.id}"


@pytest.mark.asyncio
async def test_handoff_im_receipt_confirms_same_message_number() -> None:
    async def send_with_receipt(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(client_msg_no=kwargs["client_msg_no"])

    with patch.object(
        human_handoff_service.wukongim_client,
        "send_system_message",
        new=AsyncMock(side_effect=send_with_receipt),
    ) as send:
        await human_handoff_service._notify_handoff(
            content="转人工", channel_id="channel-1", channel_type=251,
            client_msg_no="handoff-1",
        )
    assert send.await_args.kwargs["client_msg_no"] == "handoff-1"

    with patch.object(
        human_handoff_service.wukongim_client,
        "send_system_message",
        new=AsyncMock(return_value=SimpleNamespace(client_msg_no="other")),
    ):
        with pytest.raises(RuntimeError, match="mismatched handoff"):
            await human_handoff_service._notify_handoff(
                content="转人工", channel_id="channel-1", channel_type=251,
                client_msg_no="handoff-1",
            )


@pytest.mark.asyncio
async def test_ai_handoff_unique_conflict_reads_committed_receipt() -> None:
    project_id, visitor_id = uuid4(), uuid4()
    source_id = f"ai:{uuid4()}"
    reason = "转人工"
    saved_receipt = {"request_id": str(uuid4()), "status": "active"}
    saved = SimpleNamespace(
        id=uuid4(), deleted_at=None,
        request_metadata={
            "handoff_action_digest": human_handoff_service._handoff_action_digest(
                reason, None,
                human_handoff_service.build_visitor_channel_id(visitor_id),
                human_handoff_service.CHANNEL_TYPE_CUSTOMER_SERVICE,
            ),
            "handoff_receipt": saved_receipt,
            "notification_status": "submitted",
        },
    )

    class _RacingDB(_FakeDB):
        def flush(self) -> None:
            self.results[ManualServiceRequest] = saved
            raise IntegrityError("unique conflict", {}, Exception("duplicate"))

    db = _RacingDB({
        Tag: SimpleNamespace(id=MANUAL_SERVICE_TAG_ID, deleted_at=None),
        VisitorTag: SimpleNamespace(deleted_at=None),
        ManualServiceRequest: None,
    })
    visitor = SimpleNamespace(
        id=visitor_id, project_id=project_id, ai_disabled=False,
        service_mode="auto",
    )
    with patch.object(human_handoff_service, "_notify_handoff",
                      new=AsyncMock()) as notify:
        result = await human_handoff_service.request_human_handoff(
            db=db, project=SimpleNamespace(id=project_id), visitor=visitor,
            reason=reason, source_message_id=source_id,
            strict_source_receipt=True,
        )
    assert result == {**saved_receipt, "notification_status": "submitted"}
    assert db.commits == 0
    notify.assert_not_awaited()


def test_ai_handoff_committed_before_receipt_requires_review() -> None:
    project_id, visitor_id = uuid4(), uuid4()
    source_id = f"ai:{uuid4()}"
    channel_id = human_handoff_service.build_visitor_channel_id(visitor_id)
    saved = SimpleNamespace(
        id=uuid4(), deleted_at=None, request_metadata={},
        reason="转人工", channel=None, channel_id=channel_id,
        channel_type=human_handoff_service.CHANNEL_TYPE_CUSTOMER_SERVICE,
    )
    db = _FakeDB({ManualServiceRequest: saved})
    digest = human_handoff_service._handoff_action_digest(
        "转人工", None, channel_id,
        human_handoff_service.CHANNEL_TYPE_CUSTOMER_SERVICE,
    )
    receipt = human_handoff_service._saved_handoff_receipt(
        db, project_id, visitor_id, source_id, digest,
    )
    assert receipt is not None
    assert receipt["status"] == "unknown"
    assert receipt["request_id"] == str(saved.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("available_staff", [True, False])
async def test_new_handoff_notifies_after_assignment_or_queue(available_staff):
    project_id = uuid4()
    visitor = SimpleNamespace(
        id=uuid4(), project_id=project_id, platform_id=uuid4(),
        service_status="new", is_unassigned=True, ai_disabled=False,
        service_mode="auto",
    )
    db = _FakeDB({
        Tag: SimpleNamespace(id=MANUAL_SERVICE_TAG_ID, deleted_at=None),
        VisitorTag: SimpleNamespace(deleted_at=None),
    })
    queue = SimpleNamespace(id=uuid4(), status="waiting", position=1,
                            priority=0, channel_id=None, channel_type=None)
    assignment = SimpleNamespace(
        success=True, assigned_staff_id=uuid4() if available_staff else None,
        session=SimpleNamespace(id=uuid4()), message="assigned or queued",
        waiting_queue=None if available_staff else queue,
    )
    with (
        patch.object(human_handoff_service, "transfer_to_staff",
                     new=AsyncMock(return_value=assignment)) as transfer,
        patch.object(human_handoff_service, "notify_visitor_profile_updated",
                     new=AsyncMock()),
        patch.object(human_handoff_service, "_notify_handoff",
                     new=AsyncMock()) as notify,
    ):
        result = await human_handoff_service.request_human_handoff(
            db=db, project=SimpleNamespace(id=project_id), visitor=visitor,
            reason="转人工", source_message_id="new-handoff", channel="wecom",
        )
    assert visitor.ai_disabled is True
    assert visitor.service_mode == "manual"
    assert transfer.await_args.kwargs["ai_disabled"] is True
    assert transfer.await_args.kwargs["add_to_queue_if_no_staff"] is True
    assert result["status"] == ("active" if available_staff else "waiting")
    notify.assert_awaited_once()
