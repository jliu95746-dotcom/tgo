"""Channel ingestion is independent of model latency and target secrets."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.db.turn_context import CustomerTurnContext
from app.domain.entities import NormalizedMessage
from app.domain.services import (
    dispatcher,
    slack_turn_capture,
    turn_context,
    wecom_turn_capture,
)
from app.domain.services.conversation_scope import upstream_scope
from app.domain.services.turn_media_upload import safe_slack_url


def message(**changes):
    return NormalizedMessage(
        source="slack",
        platform_id=str(uuid4()),
        from_uid="customer",
        platform_api_key="fixture-channel",
        platform_type="slack",
        content="问题",
        **changes
    )


def test_encrypted_context_survives_reopen_and_does_not_contain_channel_secrets(
    monkeypatch,
):
    monkeypatch.setattr(
        settings, "saas_internal_token", SecretStr("fixture-internal-secret")
    )
    original = message(
        extra={
            "message_id": "one",
            "slack": {
                "channel": "room",
                "thread_ts": "thread",
                "bot_token": "fixture-private-bot",
            },
        }
    )
    row = CustomerTurnContext(encrypted_payload=turn_context.seal(original))
    assert (
        "fixture-private" not in row.encrypted_payload
        and "问题" not in row.encrypted_payload
    )
    platform = SimpleNamespace(id=original.platform_id, api_key="current-channel-key")
    restored = turn_context.unseal(row, platform)
    assert (
        restored.content == "问题" and restored.platform_api_key == "current-channel-key"
    )
    assert "bot_token" not in restored.extra["slack"]
    with pytest.raises(Exception):
        turn_context.unseal(row, SimpleNamespace(id=str(uuid4()), api_key="other"))


def test_slack_consecutive_root_messages_share_scope_but_threads_and_senders_do_not():
    first = message(
        extra={
            "message_id": "1",
            "slack": {"channel": "room", "thread_ts": "1", "conversation_thread": ""},
        }
    )
    second = first.model_copy(
        update={
            "extra": {
                "message_id": "2",
                "slack": {
                    "channel": "room",
                    "thread_ts": "2",
                    "conversation_thread": "",
                },
            }
        }
    )
    assert (
        upstream_scope(first)["reception_conversation"]
        == upstream_scope(second)["reception_conversation"]
    )
    thread = second.model_copy(
        update={"extra": {"slack": {"channel": "room", "thread_ts": "1"}}}
    )
    assert (
        upstream_scope(first)["reception_conversation"]
        != upstream_scope(thread)["reception_conversation"]
    )
    other = first.model_copy(update={"from_uid": "other-member"})
    assert (
        upstream_scope(first)["reception_author"]
        != upstream_scope(other)["reception_author"]
    )


@pytest.mark.asyncio
async def test_dispatcher_captures_without_waiting_for_a_reply(monkeypatch):
    capture = AsyncMock()
    monkeypatch.setattr(turn_context, "capture", capture)
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(type="slack")))
    client = SimpleNamespace(durable_turns=True, chat_completion=AsyncMock())
    incoming = message(extra={"message_id": "one"})
    assert await dispatcher.process_message(incoming, db, client, None) is None
    capture.assert_awaited_once_with(db, incoming)
    client.chat_completion.assert_not_called()


class FakeSession:
    def __init__(self):
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass


@pytest.mark.asyncio
async def test_slack_all_attachments_registered_before_any_upload(monkeypatch):
    capture = AsyncMock()
    monkeypatch.setattr(slack_turn_capture, "capture", capture)
    db = FakeSession()
    platform = SimpleNamespace(id=uuid4(), api_key="fixture")
    await slack_turn_capture.capture_event(
        lambda: db,
        platform,
        {
            "user": "customer",
            "channel": "room",
            "ts": "100.1",
            "text": "图片加语音是同一个问题",
            "files": [
                {
                    "id": "image",
                    "mimetype": "image/png",
                    "url_private": "https://files.slack.com/a",
                },
                {
                    "id": "voice",
                    "mimetype": "audio/ogg",
                    "url_private": "https://files.slack.com/b",
                },
            ],
        },
    )
    messages = [call.args[1] for call in capture.await_args_list]
    assert [part.extra["msg_type"] for part in messages] == [1, 2, 4]
    assert all(part.extra["media_pending"] for part in messages[1:])
    assert len({part.extra["message_id"] for part in messages}) == 3
    assert (
        len({upstream_scope(part)["reception_conversation"] for part in messages}) == 1
    )
    assert all(call.kwargs == {"commit": False} for call in capture.await_args_list)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_wecom_native_staff_history_cannot_trigger_auto_reply(monkeypatch):
    capture = AsyncMock()
    monkeypatch.setattr(wecom_turn_capture, "capture", capture)
    for origin in (4, 5, "4", "5"):
        record = SimpleNamespace(
            msg_type="text",
            source_type="wecom_kf",
            raw_payload={"kf_sync_msg": {"origin": origin}},
        )
        await wecom_turn_capture.capture_inbox(None, record)
    capture.assert_not_awaited()


@pytest.mark.parametrize(
    "url",
    [
        "http://files.slack.com/a",
        "https://files.slack.com.attacker.test/a",
        "https://user:pass@files.slack.com/a",
        "https://127.0.0.1/a",
        "https://files.slack.com:444/a",
        None,
    ],
)
def test_media_credentials_never_go_to_untrusted_download_hosts(url):
    with pytest.raises(ValueError):
        safe_slack_url(url)


def test_media_download_accepts_only_original_slack_host():
    assert (
        safe_slack_url("https://files.slack.com/files-pri/one")
        == "https://files.slack.com/files-pri/one"
    )
