"""Workbench responses and activity timestamps preserve their public contract."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
import httpx
import pytest
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles

from app.api.v1.endpoints import (
    conversations, staff as staff_api, visitors as visitor_api,
)
from app.core.exceptions import TGOAPIException
from app.models import (
    ChannelMemoryClearance, Staff, VisitorActivity, VisitorAssignmentRule,
    VisitorWaitingQueue, Tag, VisitorTag,
)
from app.models.tag import TagCategory
from app.schemas.staff import StaffListParams
from app.schemas.visitor import (
    VisitorResponse, VisitorBasicResponse, set_visitor_list_display_nickname,
)
from app.schemas.visitor_activity import VisitorActivityCreateRequest
from app.services.im_access import issue_visitor_im_token
from app.services import platform_message_client
from app.schemas.wukongim import WuKongIMConversation
from app.utils.encoding import build_visitor_channel_id
from tests import test_im_access as im_fixtures
from tests import test_conversation_tenant_boundary as conversation_fixtures
from tests import test_staff_visitor_read_access as read_fixtures
from tests import test_visitor_profile_access as profile_fixtures

records = im_fixtures.records
channel_app = conversation_fixtures.channel_app
profile_api = profile_fixtures.profile_api
staff_read_api = read_fixtures.staff_read_api
STAMP = datetime(2025, 1, 2, 8, 30)


@compiles(ARRAY, "sqlite")
def compile_array(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def workbench_records(records):
    db, projects, staff, platforms, visitors = records
    for model in (VisitorActivity, VisitorAssignmentRule, VisitorWaitingQueue):
        model.__table__.create(db.get_bind())
    platforms[0].api_key = "synthetic-workbench-platform-key"
    db.commit()
    return db, projects, staff, platforms, visitors


def activity_payload(visitor, previous, time_mode):
    payload = {
        "visitor_id": visitor.id, "activity_type": "page_view",
        "title": "Updated activity",
    }
    if previous:
        payload["id"] = previous.id
    if time_mode == "null":
        payload["occurred_at"] = None
    elif time_mode == "explicit":
        payload["occurred_at"] = STAMP + timedelta(days=1)
    return VisitorActivityCreateRequest.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("time_mode", ["omitted", "null", "explicit"])
async def test_activity_time_is_defaulted_or_preserved_when_not_supplied(
    workbench_records, existing, time_mode,
):
    db, _, _, platforms, visitors = workbench_records
    visitor = visitors[0]
    previous = None
    if existing:
        previous = VisitorActivity(
            project_id=visitor.project_id, visitor_id=visitor.id,
            activity_type="page_view", title="Original", occurred_at=STAMP,
        )
        db.add(previous)
        db.commit()
    req = activity_payload(visitor, previous, time_mode)
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=issue_visitor_im_token(visitor, timedelta(hours=1)),
    )
    before = datetime.utcnow().replace(microsecond=0)
    result = await visitor_api.record_visitor_activity(
        req, Request({"type": "http", "headers": []}), credentials,
        platforms[0].api_key, db,
    )
    db.expire_all()
    saved = db.get(VisitorActivity, result.id)
    assert saved.title == "Updated activity"
    assert saved.visitor_id == visitor.id
    assert db.query(VisitorActivity).count() == 1
    if time_mode == "explicit":
        assert saved.occurred_at == STAMP + timedelta(days=1)
    elif existing:
        assert saved.occurred_at == STAMP
    else:
        assert before <= saved.occurred_at <= datetime.utcnow()
    assert result.occurred_at == saved.occurred_at
    if existing:
        assert result.id == previous.id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing-proof", "other-visitor", "other-activity"])
async def test_activity_timestamp_handling_cannot_bypass_ownership(
    workbench_records, kind,
):
    db, _, _, platforms, visitors = workbench_records
    other = VisitorActivity(
        project_id=visitors[1].project_id, visitor_id=visitors[1].id,
        activity_type="page_view", title="Other project", occurred_at=STAMP,
    )
    db.add(other)
    db.commit()
    credentials = None if kind == "missing-proof" else HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=issue_visitor_im_token(visitors[0], timedelta(hours=1)),
    )
    target = visitors[1] if kind == "other-visitor" else visitors[0]
    req = activity_payload(target, other if kind == "other-activity" else None,
                           "null")
    with pytest.raises((HTTPException, TGOAPIException)) as failure:
        await visitor_api.record_visitor_activity(
            req, Request({"type": "http", "headers": []}), credentials,
            platforms[0].api_key, db,
        )
    assert failure.value.status_code in {401, 404}
    db.rollback()
    db.refresh(other)
    assert other.title == "Other project" and other.occurred_at == STAMP


@pytest.mark.asyncio
async def test_staff_list_keeps_pagination_filters_and_project_scope(
    workbench_records,
):
    db, _, staff, _, _ = workbench_records
    staff[0].nickname = "测试接待员"
    staff[0].avatar_url = "/avatars/test.png"
    staff[0].status = "busy"
    second = Staff(
        project_id=staff[0].project_id, username="second", role="user",
        status="online", password_hash="synthetic-private-password-hash",
    )
    deleted = Staff(
        project_id=staff[0].project_id, username="deleted", role="user",
        password_hash="unused", deleted_at=datetime.utcnow(),
    )
    db.add_all([second, deleted])
    db.commit()
    result = await staff_api.list_staff(
        StaffListParams(limit=1, offset=0, status="busy"), db, staff[0],
    )
    assert result.pagination.model_dump() == {
        "total": 1, "limit": 1, "offset": 0,
        "has_next": False, "has_prev": False,
    }
    assert [item.id for item in result.data] == [staff[0].id]
    assert result.data[0].nickname == "测试接待员"
    assert result.data[0].avatar_url == "/avatars/test.png"
    assert result.data[0].is_working is True
    assert "password" not in result.model_dump_json()
    empty = await staff_api.list_staff(
        StaffListParams(limit=1, offset=3), db, staff[0],
    )
    assert empty.data == [] and empty.pagination.total == 2
    assert empty.pagination.has_prev is True
    assert empty.pagination.has_next is False


@pytest.mark.parametrize("working", [None, False, True])
def test_staff_response_keeps_nullable_working_status_and_excludes_password(
    workbench_records, working,
):
    member = workbench_records[2][0]
    result = staff_api._build_staff_response(member, working)
    assert result.id == member.id and result.project_id == member.project_id
    assert result.is_working is working
    assert "password" not in result.model_dump_json()


@pytest.mark.parametrize("kind", [VisitorResponse, VisitorBasicResponse])
def test_nickname_enrichment_returns_original_list_and_objects(kind):
    # Construct only the fields used for display; no alternate response schema.
    response = kind.model_construct(nickname="Nickname", nickname_zh="中文昵称")
    items = [response]
    assert set_visitor_list_display_nickname(items, "zh") is items
    assert items[0] is response
    assert response.display_nickname == "中文昵称"


@pytest.mark.asyncio
async def test_waiting_conversations_send_only_current_project_channels(
    workbench_records, monkeypatch,
):
    db, _, staff, _, visitors = workbench_records
    monkeypatch.setattr(conversations, "restricted_staff", lambda user: False)
    for visitor in visitors:
        db.add(VisitorWaitingQueue(
            project_id=visitor.project_id, visitor_id=visitor.id,
            status="waiting", expired_at=datetime.utcnow() + timedelta(hours=1),
        ))
    db.commit()
    channel = build_visitor_channel_id(visitors[0].id)
    sync = AsyncMock(return_value=[WuKongIMConversation(
        channel_id=channel, channel_type=251, unread=7, timestamp=1,
        last_msg_seq=0, last_client_msg_no="test", version=1, recents=[],
    )])
    monkeypatch.setattr(conversations.wukongim_client,
                        "sync_conversations_by_channels", sync)
    result = await conversations.sync_waiting_conversations(3, 1, 0, db, staff[0])
    assert result.pagination.total == 1
    assert result.conversations[0].unread == 0
    sync.assert_awaited_once_with(
        uid=f"{staff[0].id}-staff", msg_count=3,
        channels=[{"channel_id": channel, "channel_type": 251}],
    )
    sync.reset_mock()
    empty = await conversations.sync_waiting_conversations(3, 1, 1, db, staff[0])
    assert empty.conversations == []
    assert empty.pagination.total == 1 and empty.pagination.has_prev is True
    sync.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cleared,start,expected", [(None, 0, 0), (10, 0, 11),
                                                   (10, 15, 15)])
async def test_history_starts_after_clearance_without_skipping_newer_request(
    channel_app, cleared, start, expected,
):
    app, db, _, staff, visitors, downstream = channel_app
    channel = build_visitor_channel_id(visitors[0].id)
    if cleared is not None:
        record = ChannelMemoryClearance(
            project_id=staff[0].project_id, user_id=staff[0].id,
            user_type="staff", channel_id=channel, channel_type=251,
            cleared_message_seq=cleared,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        assert isinstance(record.cleared_message_seq, int)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                base_url="http://test") as client:
        response = await client.post("/conversations/messages", json={
            "channel_id": channel, "channel_type": 251,
            "start_message_seq": start,
        })
    assert response.status_code == 200
    assert downstream[0].await_args.kwargs["start_message_seq"] == expected


@pytest.mark.asyncio
async def test_unset_service_mode_does_not_request_native_handback(
    workbench_records, monkeypatch,
):
    def unexpected_client(**kwargs):
        raise AssertionError("Unset mode must not start a remote operation")

    monkeypatch.setattr(platform_message_client.httpx, "AsyncClient", unexpected_client)
    await platform_message_client.prepare_native_mode(workbench_records[4][0], None)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "zh-CN"])
async def test_tagged_visitor_list_keeps_language_pagination_and_project_scope(
    staff_read_api, language,
):
    app, db, _, staff, _, visitors, _, _ = staff_read_api
    tag_ids = []
    for index, visitor in enumerate(visitors):
        tag = Tag(
            project_id=visitor.project_id, name=f"VIP-{index}",
            name_zh="会员", category=TagCategory.VISITOR,
        )
        tag_ids.append(tag.id)
        db.add(tag)
        db.flush()
        db.add(VisitorTag(project_id=visitor.project_id,
                          visitor_id=visitor.id, tag_id=tag.id))
    db.commit()
    response = await read_fixtures.get(
        app, "/visitors", {**read_fixtures.proof(staff[0]),
                            "Accept-Language": language},
        params=[("tag_ids", tag_ids[0]), ("tag_ids", tag_ids[1]), ("limit", 1)],
    )
    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body["data"]] == [str(visitors[0].id)]
    assert body["pagination"] == {
        "total": 1, "limit": 1, "offset": 0,
        "has_next": False, "has_prev": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("visitor_status,include_closed,count", [
    ("active", False, 1), ("closed", False, 0), ("closed", True, 1),
])
async def test_conversation_enrichment_preserves_project_and_status_filters(
    staff_read_api, visitor_status, include_closed, count,
):
    app, db, projects, staff, _, visitors, _, _ = staff_read_api
    visitors[0].service_status = visitor_status
    db.commit()

    def conversation(channel):
        return WuKongIMConversation(
            channel_id=channel, channel_type=251, unread=0, timestamp=1,
            last_msg_seq=0, last_client_msg_no="test", version=1, recents=[],
        )

    channels = [conversation(build_visitor_channel_id(visitor.id))
                for visitor in visitors]
    channels.append(conversation("invalid-visitor-channel"))
    result = await conversations._build_channels_for_conversations(
        db, channels, projects[0].id, "zh", "zh-CN", include_closed,
    )
    assert len(result) == count
    if count:
        assert result[0].channel_id == build_visitor_channel_id(visitors[0].id)
        assert result[0].extra["assigned_staff_id"] == str(staff[0].id)
    assert build_visitor_channel_id(visitors[1].id) not in {
        item.channel_id for item in result
    }
