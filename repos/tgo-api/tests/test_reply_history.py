"""Orphaned AI anchors must not remain typing forever in either history API."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.schemas.ai_runs import ReplyRun
from app.schemas.wukongim import (
    WuKongIMChannelMessageSyncResponse,
    WuKongIMMessage,
)
from app.services import reply_history
from app.services.run_registry import RegistryUnavailable


PROJECT = str(uuid4())
VISITOR = uuid4()
CHANNEL = f"{VISITOR}-vtr"
CLIENT_NO = "ai_" + "a" * 32
NOW = 1800000000


def history(**changes):
    message = WuKongIMMessage(
        header={"no_persist": 0, "red_dot": 0, "sync_once": 0},
        setting=2,
        message_id=1,
        message_seq=1,
        client_msg_no=CLIENT_NO,
        from_uid="operator-staff",
        channel_id=CHANNEL,
        channel_type=251,
        timestamp=NOW - 300,
        payload={"type": 100, "content": ""},
    ).model_copy(update=changes)
    return WuKongIMChannelMessageSyncResponse(
        messages=[message],
        start_message_seq=0,
        end_message_seq=1,
        more=0,
    )


def run(**changes):
    return ReplyRun(
        project_id=PROJECT,
        client_msg_no=CLIENT_NO,
        channel_id=CHANNEL,
        channel_type=251,
    ).model_copy(update=changes)


async def reconcile(monkeypatch, response, item=None, error=None):
    registry = SimpleNamespace(get=AsyncMock(return_value=item, side_effect=error))
    monkeypatch.setattr(reply_history, "run_registry", registry)
    monkeypatch.setattr(reply_history.time, "time", lambda: NOW)
    result = await reply_history.reconcile_reply_history(
        response,
        project_id=PROJECT,
        channel_id=CHANNEL,
        channel_type=251,
    )
    return result, registry


@pytest.mark.asyncio
async def test_expired_anchor_becomes_incomplete_without_rewriting_stored_message(
    monkeypatch,
):
    original = history()
    result, registry = await reconcile(monkeypatch, original)
    registry.get.assert_awaited_once_with(PROJECT, CLIENT_NO)
    assert result.messages[0].end == 1
    assert "未正常完成" in result.messages[0].error
    assert "已停止" not in result.messages[0].error
    assert result.messages[0].client_msg_no == CLIENT_NO
    assert result.messages[0].payload == original.messages[0].payload
    assert original.messages[0].end is None
    assert original.messages[0].error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "publishing", "cancel_requested"])
async def test_live_ownership_keeps_typing_even_when_message_is_old(
    monkeypatch, status
):
    original = history(timestamp=NOW - 86400)
    result, _ = await reconcile(monkeypatch, original, run(status=status))
    assert result == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"timestamp": NOW - 30},
        {"timestamp": NOW + 100},
        {"timestamp": 0},
        {"payload": {"type": 1, "content": "你好"}},
        {"client_msg_no": "legacy-reply"},
        {"from_uid": f"{VISITOR}-vtr"},
        {"channel_id": "another-vtr"},
        {"channel_type": 1},
        {"end": 1},
        {"error": "本次回复已停止"},
        {"event_meta": {"completed": True, "has_events": True}},
    ],
)
async def test_recent_finished_or_unrelated_messages_are_untouched(
    monkeypatch, changes
):
    original = history(**changes)
    result, registry = await reconcile(monkeypatch, original)
    assert result == original
    registry.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"project_id": "other-project"},
        {"channel_id": "other-channel"},
        {"client_msg_no": "other-reply"},
        {"channel_type": 1},
    ],
)
async def test_mismatched_identity_is_not_a_terminal_receipt(monkeypatch, changes):
    original = history()
    result, _ = await reconcile(
        monkeypatch, original, run(status="cancelled", **changes)
    )
    assert result == original


@pytest.mark.asyncio
async def test_registry_unavailable_is_not_proof_that_reply_stopped(monkeypatch):
    original = history()
    result, _ = await reconcile(monkeypatch, original, error=RegistryUnavailable())
    assert result == original


@pytest.mark.asyncio
async def test_registry_timeout_does_not_block_history_or_claim_cancellation(
    monkeypatch,
):
    async def slow_lookup(*args):
        await asyncio.sleep(10)

    original = history()
    monkeypatch.setattr(reply_history, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    result, _ = await reconcile(monkeypatch, original, error=slow_lookup)
    assert result == original


@pytest.mark.asyncio
async def test_same_client_number_in_another_channel_is_not_replaced(monkeypatch):
    original = history()
    foreign = history(channel_id="foreign-vtr").messages[0]
    original.messages.append(foreign)
    result, _ = await reconcile(monkeypatch, original)
    assert result.messages[0].end == 1
    assert result.messages[1] == foreign
    assert result.messages[1].error is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,reason,notice",
    [
        ("cancelled", None, "本次回复已停止"),
        ("failed", "generation_failed", "未正常完成"),
        ("failed", "upstream_stop_unconfirmed", "停止尚未确认"),
        ("completed", None, "发送结果未确认"),
    ],
)
async def test_terminal_receipts_close_loading_but_preserve_snapshot(
    monkeypatch,
    status,
    reason,
    notice,
):
    original = history(
        event_meta={
            "has_events": True,
            "completed": False,
            "open_event_count": 1,
            "events": [
                {
                    "event_key": "main",
                    "status": "open",
                    "snapshot": {"kind": "text", "text": "已有内容"},
                }
            ],
        }
    )
    result, _ = await reconcile(
        monkeypatch,
        original,
        run(status=status, failure_reason=reason),
    )
    message = result.messages[0]
    assert message.end == 1
    assert notice in message.error
    assert message.event_meta["open_event_count"] == 0
    assert message.event_meta["completed"] is True
    event = message.event_meta["events"][0]
    assert event["snapshot"]["text"] == "已有内容"
    assert event["status"] == "error"
    assert original.messages[0].event_meta["open_event_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["staff", "visitor"])
async def test_both_authorized_history_endpoints_reconcile(monkeypatch, actor):
    from app.api.v1.endpoints import conversations, visitors
    from app.schemas.visitor import VisitorMessageSyncRequest
    from app.schemas.wukongim import WuKongIMChannelMessageSyncRequest

    original = history()
    await reconcile(monkeypatch, original)
    database = Mock()
    platform_id = uuid4()
    if actor == "staff":
        endpoint = conversations
        database.query.return_value.filter.return_value.first.side_effect = [
            (VISITOR,), None,
        ]
    else:
        endpoint = visitors
        database.query.return_value.filter.return_value.first.side_effect = [
            SimpleNamespace(project_id=PROJECT, id=platform_id),
            SimpleNamespace(id=VISITOR, platform_id=platform_id),
        ]
    monkeypatch.setattr(
        endpoint.wukongim_client,
        "sync_channel_messages",
        AsyncMock(return_value=original),
    )
    if actor == "staff":
        result = await endpoint.sync_channel_messages(
            WuKongIMChannelMessageSyncRequest(channel_id=CHANNEL, channel_type=251),
            current_user=SimpleNamespace(
                project_id=PROJECT, id=uuid4(), username="test"
            ),
            db=database,
        )
    else:
        result = await endpoint.sync_visitor_channel_messages(
            VisitorMessageSyncRequest(channel_id=CHANNEL, channel_type=251),
            db=database,
            x_platform_api_key="test-platform-key",
        )
    assert result.messages[0].end == 1
    assert "未正常完成" in result.messages[0].error
