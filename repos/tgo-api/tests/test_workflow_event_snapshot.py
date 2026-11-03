"""Closed-session snapshots never read another session or raw identifiers."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models import VisitorSession
from app.models.session_command_delivery import SessionCommandDelivery
from app.models.workflow_event_subscription import WorkflowEventSubscription
from app.schemas.wukongim import WuKongIMChannelMessageSyncResponse
from app.services import workflow_event_snapshot as snapshot
from tests.test_session_handoff_summary import _message


def source():
    visitor = uuid4()
    session = VisitorSession(
        id=uuid4(),
        project_id=uuid4(),
        visitor_id=visitor,
        status="closed",
        created_at=datetime.fromtimestamp(200, timezone.utc),
        closed_at=datetime.fromtimestamp(210, timezone.utc),
        last_message_seq=8,
    )
    command = SessionCommandDelivery(
        id=uuid4(),
        project_id=session.project_id,
        visitor_id=visitor,
        session_id=session.id,
        kind="close",
        channel_id=f"{visitor}-vtr",
        channel_type=251,
    )
    return command, session


def subscription(command):
    return WorkflowEventSubscription(
        project_id=command.project_id,
        enabled_by_staff_id=uuid4(),
    )


def history(command, rows):
    for row in rows:
        row.channel_id = command.channel_id
    return WuKongIMChannelMessageSyncResponse(
        start_message_seq=0,
        end_message_seq=9,
        more=1,
        messages=rows,
    )


def test_snapshot_bounds_time_sequence_clearance_and_completed_content():
    command, session = source()
    visitor = f"{session.visitor_id}-vtr"
    result = snapshot.closed_session_data(
        command,
        session,
        history(
            command,
            [
                _message(1, 199, visitor, "旧会话"),
                _message(2, 201, visitor, "已清理"),
                _message(3, 202, visitor, "邮箱 person@example.com 电话 13800138000"),
                _message(4, 203, "bot-agent", "未完成", kind=100, complete=False),
                _message(5, 204, visitor, "https://secret.example/image.png", kind=2),
                _message(6, 205, "operator-staff", "已解释退货政策"),
                _message(7, 211, visitor, "关闭后消息"),
                _message(9, 209, visitor, "新会话序号"),
            ],
        ),
        earliest_seq=3,
    )
    text = str(result)
    assert "旧会话" not in text and "已清理" not in text
    assert "关闭后消息" not in text and "新会话序号" not in text
    assert "未完成" not in text and "person@example.com" not in text
    assert "13800138000" not in text and "secret.example" not in text
    assert "[邮箱]" in text and "[数字标识]" in text and "[图片]" in text
    assert result["sampled_message_count"] == 3
    assert result["snapshot_scope"] == "bounded_session_excerpts"


@pytest.mark.parametrize(
    "change",
    [
        {"last_message_seq": None},
        {"last_message_seq": 0},
        {"closed_at": None},
        {"status": "open"},
    ],
)
@pytest.mark.asyncio
async def test_missing_closed_boundary_never_fetches_latest(change, monkeypatch):
    command, session = source()
    for key, value in change.items():
        setattr(session, key, value)
    client = MagicMock(enabled=True, sync_channel_messages=AsyncMock())
    monkeypatch.setattr(snapshot, "wukongim_client", client)
    db = MagicMock()
    db.scalar.return_value = session
    with pytest.raises(snapshot.SnapshotUnavailable):
        await snapshot.load_closed_session_snapshot(
            db,
            subscription(command),
            command,
        )
    client.sync_channel_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_loader_requests_fixed_window_and_honors_clearance(monkeypatch):
    command, session = source()
    sub = subscription(command)
    reader = sub.enabled_by_staff_id
    db = MagicMock()
    db.scalar.return_value = session
    db.scalars.return_value.all.return_value = []
    client = MagicMock(
        enabled=True,
        sync_channel_messages=AsyncMock(
            return_value=history(
                command,
                [
                    _message(8, 209, f"{session.visitor_id}-vtr", "申请退货"),
                ],
            ),
        ),
    )
    monkeypatch.setattr(snapshot, "wukongim_client", client)
    result = await snapshot.load_closed_session_snapshot(
        db,
        sub,
        command,
    )
    args = client.sync_channel_messages.call_args.kwargs
    assert args["start_message_seq"] == 8
    assert args["end_message_seq"] == 0 and args["pull_mode"] == 0
    assert args["channel_id"] == command.channel_id
    assert args["login_uid"] == f"{reader}-staff" and args["limit"] == 100
    assert result["sampled_message_count"] == 1
    clearance = MagicMock(cleared_message_seq=8)
    db.scalars.return_value.all.return_value = [clearance]
    with pytest.raises(snapshot.SnapshotUnavailable):
        await snapshot.load_closed_session_snapshot(db, sub, command)
    assert client.sync_channel_messages.await_count == 1


def test_wrong_channel_fails_closed_and_other_visitor_is_not_a_customer():
    command, session = source()
    row = _message(3, 202, "other-vtr", "别人的话")
    batch = history(command, [row])
    with pytest.raises(snapshot.SnapshotUnavailable):
        snapshot.closed_session_data(command, session, batch)
    row.from_uid = f"{session.visitor_id}-vtr"
    row.channel_id = "other-channel"
    with pytest.raises(snapshot.SnapshotUnavailable):
        snapshot.closed_session_data(command, session, batch)


def test_snapshot_is_bounded_and_partial_with_masked_urls_and_secrets():
    command, session = source()
    session.last_message_seq = 30
    result = snapshot.closed_session_data(
        command,
        session,
        history(
            command,
            [
                _message(
                    index,
                    205,
                    f"{session.visitor_id}-vtr",
                    "token: abc-private 访问 https://example.test/path 客户反馈",
                )
                for index in range(1, 21)
            ],
        ),
    )
    assert result["sampled_message_count"] == 12
    assert result["has_earlier_messages"] is True
    assert len(result["excerpts"]) == 12
    assert "abc-private" not in str(result) and "example.test" not in str(result)
    assert "客户反馈" in str(result)


@pytest.mark.asyncio
async def test_source_from_other_tenant_is_rejected_without_im(monkeypatch):
    command, session = source()
    sub = subscription(command)
    sub.project_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = session
    client = MagicMock(enabled=True, sync_channel_messages=AsyncMock())
    monkeypatch.setattr(snapshot, "wukongim_client", client)
    with pytest.raises(snapshot.SnapshotUnavailable):
        await snapshot.load_closed_session_snapshot(db, sub, command)
    client.sync_channel_messages.assert_not_awaited()
