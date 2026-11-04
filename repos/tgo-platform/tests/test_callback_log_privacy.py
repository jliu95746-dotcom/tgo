"""Channel callbacks retain messages without logging customer payloads or secrets."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.dingtalk_utils import dingtalk_compute_signature
from app.api.v1 import callbacks


PRIVATE_VALUE = "private@example.com"


class FakeRequest:
    def __init__(
        self,
        body: bytes = b"",
        *,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.query_params = query or {}
        self.headers = headers or {}
        self.state = SimpleNamespace(request_id="safe-request-id")

    async def body(self) -> bytes:
        return self._body


class FakeDatabase:
    def __init__(self, commit_error: Exception | None = None) -> None:
        self.added: list[object] = []
        self.commit_error = commit_error
        self.rollbacks = 0

    def add(self, record: object) -> None:
        self.added.append(record)

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1


def platform(config: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), config=config or {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    (
        callbacks._handle_wecom_bot_webhook,
        callbacks._handle_feishu_bot_webhook,
        callbacks._handle_dingtalk_bot_webhook,
        callbacks._handle_telegram_webhook,
    ),
)
async def test_malformed_callback_does_not_log_body_or_headers(
    handler, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    request = FakeRequest(
        f'{{"content":"{PRIVATE_VALUE}"'.encode(),
        headers={"X-DingTalk-Sign": PRIVATE_VALUE},
    )
    with caplog.at_level(logging.DEBUG):
        response = await handler(platform(), request, FakeDatabase())

    assert response.status_code == 400
    assert PRIVATE_VALUE not in caplog.text
    assert PRIVATE_VALUE not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_wecom_bot_decrypted_message_is_stored_without_logging_content(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    payload = {
        "msgid": "fixture-message",
        "from": {"userid": PRIVATE_VALUE},
        "msgtype": "text",
        "text": {"content": PRIVATE_VALUE},
    }
    monkeypatch.setattr(
        callbacks, "_wecom_decrypt_message", lambda *_args: json.dumps(payload)
    )
    ciphertext = "fixture-ciphertext"
    request = FakeRequest(
        json.dumps({"encrypt": ciphertext}).encode(),
        query={
            "timestamp": "1710000000",
            "nonce": "fixture-nonce",
            "msg_signature": callbacks.compute_msg_signature(
                "fixture-token", "1710000000", "fixture-nonce", ciphertext
            ),
        },
    )
    database = FakeDatabase()
    with caplog.at_level(logging.DEBUG):
        response = await callbacks._handle_wecom_bot_webhook(
            platform({"token": "fixture-token", "encoding_aes_key": "fixture-key"}),
            request,
            database,
        )

    assert response == {"ok": True}
    assert len(database.added) == 1
    assert database.added[0].content == PRIVATE_VALUE
    assert PRIVATE_VALUE not in caplog.text


@pytest.mark.asyncio
async def test_feishu_decrypted_event_and_verification_token_are_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        callbacks,
        "feishu_decrypt_message",
        lambda *_args: json.dumps(
            {
                "type": "url_verification",
                "challenge": "fixture-challenge",
                "token": "wrong",
            }
        ),
    )
    request = FakeRequest(b'{"encrypt":"fixture-ciphertext"}')
    with caplog.at_level(logging.DEBUG):
        response = await callbacks._handle_feishu_bot_webhook(
            platform(
                {"encrypt_key": "fixture-key", "verification_token": PRIVATE_VALUE}
            ),
            request,
            FakeDatabase(),
        )

    assert response.status_code == 403
    assert PRIVATE_VALUE not in caplog.text


@pytest.mark.asyncio
async def test_dingtalk_sender_and_body_are_not_logged(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    request = FakeRequest(
        json.dumps(
            {
                "msgId": "fixture-message",
                "senderId": PRIVATE_VALUE,
                "msgtype": "text",
                "text": {"content": PRIVATE_VALUE},
            }
        ).encode(),
        headers={"X-DingTalk-Sign": PRIVATE_VALUE},
    )
    database = FakeDatabase()
    with caplog.at_level(logging.DEBUG):
        response = await callbacks._handle_dingtalk_bot_webhook(
            platform(), request, database
        )

    assert response == {"ok": True}
    assert len(database.added) == 1
    assert database.added[0].content == PRIVATE_VALUE
    assert PRIVATE_VALUE not in caplog.text
    assert PRIVATE_VALUE not in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    (
        {},
        {"X-DingTalk-Timestamp": "1710000000000"},
        {"X-DingTalk-Sign": "fixture-signature"},
    ),
)
async def test_dingtalk_configured_secret_requires_both_signature_headers(
    headers: dict[str, str],
) -> None:
    request = FakeRequest(
        b'{"msgId":"fixture-message","senderId":"fixture-user","text":{"content":"test"}}',
        headers=headers,
    )
    database = FakeDatabase()
    response = await callbacks._handle_dingtalk_bot_webhook(
        platform({"app_secret": "fixture-secret"}), request, database
    )

    assert response.status_code == 403
    assert database.added == []


@pytest.mark.asyncio
async def test_dingtalk_valid_signature_still_stores_message() -> None:
    timestamp = "1710000000000"
    request = FakeRequest(
        b'{"msgId":"fixture-message","senderId":"fixture-user","text":{"content":"test"}}',
        headers={
            "X-DingTalk-Timestamp": timestamp,
            "X-DingTalk-Sign": dingtalk_compute_signature(timestamp, "fixture-secret"),
        },
    )
    database = FakeDatabase()
    response = await callbacks._handle_dingtalk_bot_webhook(
        platform({"app_secret": "fixture-secret"}), request, database
    )

    assert response == {"ok": True}
    assert len(database.added) == 1


@pytest.mark.asyncio
async def test_telegram_chat_and_body_are_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = FakeRequest(
        json.dumps(
            {
                "update_id": 101,
                "message": {
                    "message_id": 102,
                    "chat": {"id": PRIVATE_VALUE, "type": "private"},
                    "from": {"id": 103},
                    "text": PRIVATE_VALUE,
                },
            }
        ).encode()
    )
    database = FakeDatabase()
    with caplog.at_level(logging.DEBUG):
        response = await callbacks._handle_telegram_webhook(
            platform(), request, database
        )

    assert response == {"ok": True}
    assert len(database.added) == 1
    assert database.added[0].content == PRIVATE_VALUE
    assert PRIVATE_VALUE not in caplog.text


@pytest.mark.asyncio
async def test_wukongim_staff_identity_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = FakeRequest(query={"event": "msg.notify"})
    with caplog.at_level(logging.DEBUG):
        response = await callbacks._handle_wukongim_webhook(
            platform(),
            request,
            FakeDatabase(),
            messages=[
                {"message_id": "fixture-message", "from_uid": f"{PRIVATE_VALUE}-staff"}
            ],
        )

    assert response == {"ok": True}
    assert PRIVATE_VALUE not in caplog.text


def _integrity_error(sqlstate: str, constraint_name: str) -> IntegrityError:
    original = RuntimeError(f'{PRIVATE_VALUE} constraint "{constraint_name}"')
    original.sqlstate = sqlstate
    original.diag = SimpleNamespace(constraint_name=constraint_name)
    return IntegrityError("INSERT", {}, original)


def _valid_callback(channel: str) -> FakeRequest:
    if channel == "wecom_bot":
        return FakeRequest(
            json.dumps(
                {
                    "msgid": "fixture-message",
                    "from": {"userid": "fixture-user"},
                    "msgtype": "text",
                    "text": {"content": "测试"},
                }
            ).encode()
        )
    if channel == "feishu_bot":
        return FakeRequest(
            json.dumps(
                {
                    "header": {"event_type": "im.message.receive_v1"},
                    "event": {
                        "message": {
                            "message_id": "fixture-message",
                            "content": '{"text":"测试"}',
                            "message_type": "text",
                        },
                        "sender": {"sender_id": {"open_id": "fixture-user"}},
                    },
                }
            ).encode()
        )
    if channel == "dingtalk_bot":
        return FakeRequest(
            json.dumps(
                {
                    "msgId": "fixture-message",
                    "senderId": "fixture-user",
                    "msgtype": "text",
                    "text": {"content": "测试"},
                }
            ).encode()
        )
    return FakeRequest(
        json.dumps(
            {
                "update_id": 101,
                "message": {
                    "message_id": 102,
                    "chat": {"id": 103, "type": "private"},
                    "from": {"id": 104},
                    "text": "测试",
                },
            }
        ).encode()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel,handler,constraint_name",
    (
        (
            "wecom_bot",
            callbacks._handle_wecom_bot_webhook,
            "uq_wecom_inbox_platform_message",
        ),
        (
            "feishu_bot",
            callbacks._handle_feishu_bot_webhook,
            "uq_feishu_inbox_platform_message",
        ),
        (
            "dingtalk_bot",
            callbacks._handle_dingtalk_bot_webhook,
            "uq_dingtalk_inbox_platform_message",
        ),
        (
            "telegram",
            callbacks._handle_telegram_webhook,
            "uq_telegram_inbox_platform_message_chat",
        ),
    ),
)
@pytest.mark.parametrize(
    "sqlstate,violated_constraint,expected_status",
    (
        ("23505", "own", None),
        ("23503", "other", 500),
        ("23505", "other", 500),
    ),
)
async def test_channel_callback_only_acknowledges_its_own_duplicate(
    channel: str,
    handler,
    constraint_name: str,
    sqlstate: str,
    violated_constraint: str,
    expected_status: int | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    actual_constraint = (
        constraint_name if violated_constraint == "own" else "other_constraint"
    )
    database = FakeDatabase(_integrity_error(sqlstate, actual_constraint))
    with caplog.at_level(logging.DEBUG):
        response = await handler(platform(), _valid_callback(channel), database)

    if expected_status is None:
        assert response == {"ok": True}
    else:
        assert response.status_code == expected_status
    assert database.rollbacks == 1
    assert PRIVATE_VALUE not in caplog.text
