"""Handoff excerpts exclude stale and incomplete messages."""

from datetime import datetime, timezone
from uuid import uuid4

from app.models import VisitorSession
from app.schemas.wukongim import (
    WuKongIMChannelMessageSyncResponse, WuKongIMMessage,
)
from app.services.session_handoff_summary import summarize_messages


def _message(
    seq: int, timestamp: int, sender: str, content: str,
    *, kind: int = 1, complete: bool = True,
) -> WuKongIMMessage:
    return WuKongIMMessage(
        header={"no_persist": 0, "red_dot": 0, "sync_once": 0},
        setting=0, message_id=seq, client_msg_no=str(uuid4()),
        message_seq=seq, from_uid=sender, channel_id="test-vtr",
        channel_type=251, timestamp=timestamp,
        payload={"type": kind, "content": content},
        end=1 if complete else 0,
        event_meta={"events": [{"event_key": "main", "snapshot": {
            "kind": "text", "text": content,
        }}]} if kind == 100 else None,
    )


def test_handoff_excludes_prior_session_and_unfinished_ai() -> None:
    visitor_id = uuid4()
    session = VisitorSession(
        id=uuid4(), visitor_id=visitor_id, project_id=uuid4(),
        created_at=datetime.fromtimestamp(200, tz=timezone.utc),
    )
    history = WuKongIMChannelMessageSyncResponse(
        start_message_seq=0, end_message_seq=0, more=1,
        messages=[
            _message(4, 204, "agent-agent", "尚未完成", kind=100, complete=False),
            _message(1, 199, "visitor-vtr", "上次会话内容"),
            _message(3, 203, "staff-staff", "已收到订单号"),
            _message(2, 202, "visitor-vtr", "请查物流\n进度"),
            _message(5, 205, "system", "控制消息", kind=9),
            _message(7, 207, "system", "交接通知", kind=1),
            _message(6, 206, "agent-agent", "物流还没有查询结果", kind=100),
        ],
    )

    result = summarize_messages(session, history)

    assert result.sampled_message_count == 3
    assert result.has_earlier_messages is True
    assert [item.content for item in result.latest_customer_messages] == [
        "请查物流 进度",
    ]
    assert [item.role for item in result.latest_replies] == [
        "staff", "assistant",
    ]
    assert result.latest_replies[-1].content == "物流还没有查询结果"


def test_handoff_keeps_only_recent_excerpts_and_bounded_text() -> None:
    session = VisitorSession(
        id=uuid4(), visitor_id=uuid4(), project_id=uuid4(),
        created_at=datetime.fromtimestamp(100, tz=timezone.utc),
    )
    messages = [_message(seq, 100 + seq, "visitor-vtr", "问" * 400)
                for seq in range(1, 6)]
    result = summarize_messages(session, WuKongIMChannelMessageSyncResponse(
        start_message_seq=0, end_message_seq=0, more=0, messages=messages,
    ))
    assert [item.message_seq for item in result.latest_customer_messages] == [
        3, 4, 5,
    ]
    assert all(
        len(item.content) == 280 for item in result.latest_customer_messages
    )
    assert result.latest_replies == []


def test_handoff_excludes_ambiguous_session_start_second() -> None:
    session = VisitorSession(
        id=uuid4(), visitor_id=uuid4(), project_id=uuid4(),
        created_at=datetime.fromtimestamp(200.5, tz=timezone.utc),
    )
    result = summarize_messages(session, WuKongIMChannelMessageSyncResponse(
        start_message_seq=0, end_message_seq=0, more=0,
        messages=[
            _message(1, 200, "visitor-vtr", "上一个会话"),
            _message(2, 201, "visitor-vtr", "本次会话"),
        ],
    ))
    assert [item.content for item in result.latest_customer_messages] == [
        "本次会话",
    ]


def test_handoff_respects_staff_memory_clearance() -> None:
    session = VisitorSession(
        id=uuid4(), visitor_id=uuid4(), project_id=uuid4(),
        created_at=datetime.fromtimestamp(100, tz=timezone.utc),
    )
    history = WuKongIMChannelMessageSyncResponse(
        start_message_seq=0, end_message_seq=0, more=0,
        messages=[
            _message(10, 110, "visitor-vtr", "已清除的消息"),
            _message(11, 111, "visitor-vtr", "新消息"),
        ],
    )
    result = summarize_messages(session, history, earliest_seq=11)
    assert [item.content for item in result.latest_customer_messages] == [
        "新消息",
    ]


def test_handoff_identifies_media_without_exposing_reference() -> None:
    session = VisitorSession(
        id=uuid4(), visitor_id=uuid4(), project_id=uuid4(),
        created_at=datetime.fromtimestamp(100, tz=timezone.utc),
    )
    history = WuKongIMChannelMessageSyncResponse(
        start_message_seq=0, end_message_seq=0, more=0,
        messages=[
            _message(1, 101, "visitor-vtr", "/v1/chat/files/image", kind=2),
            _message(2, 102, "visitor-vtr", "/v1/chat/files/voice", kind=4),
            _message(3, 103, "visitor-vtr", "/v1/chat/files/file", kind=3),
        ],
    )

    result = summarize_messages(session, history)

    assert result.sampled_message_count == 3
    assert [item.content for item in result.latest_customer_messages] == [
        "[图片] 请查看原消息核对内容",
        "[语音] 请查看原消息核对内容",
        "[附件] 请查看原消息核对内容",
    ]
    assert all(
        "/v1/chat/files/" not in item.content
        for item in result.latest_customer_messages
    )


def test_handoff_includes_rich_text_and_marks_embedded_media() -> None:
    session = VisitorSession(
        id=uuid4(), visitor_id=uuid4(), project_id=uuid4(),
        created_at=datetime.fromtimestamp(100, tz=timezone.utc),
    )
    message = _message(1, 101, "visitor-vtr", "这个包有瑕疵", kind=12)
    message.payload["images"] = [{"url": "/v1/chat/files/private-image"}]
    message.payload["file"] = {"url": "/v1/chat/files/private-file"}
    history = WuKongIMChannelMessageSyncResponse(
        start_message_seq=0, end_message_seq=0, more=0, messages=[message],
    )

    result = summarize_messages(session, history)

    assert result.sampled_message_count == 1
    assert result.latest_customer_messages[0].content == (
        "这个包有瑕疵 [含图片、附件，请查看原消息核对]"
    )
