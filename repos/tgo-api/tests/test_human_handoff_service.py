"""Contract tests for persistent human handoff behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

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
