"""Staff delivery diagnostics must not disclose recipients or provider data."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.api import telegram_utils
from app.api.v1 import messages


PRIVATE_RECIPIENT = "private-recipient-marker"
PRIVATE_RECEIPT = "private-receipt-marker"
PRIVATE_URL = (
    "https://media.example.test/private-image-marker?signature=private-url-marker"
)
PRIVATE_PROVIDER_ERROR = "private-provider-error-marker"


class FakeDatabase:
    def __init__(self, platform_type: str) -> None:
        self.platform_type = platform_type

    async def scalar(self, _query: object) -> SimpleNamespace:
        return SimpleNamespace(
            type=self.platform_type,
            config={
                "bot_token": "fixture-bot-token",
                "callback_url": "https://callback.example.test/private-callback-marker",
            },
        )


def request_for(message_type: int) -> messages.SendMessageRequest:
    payload = {"type": message_type, "content": "测试回复"}
    if message_type == 2:
        payload["url"] = PRIVATE_URL
    return messages.SendMessageRequest(
        platform_api_key="fixture-platform-key",
        from_uid="fixture-staff",
        channel_id="fixture-visitor-vtr",
        channel_type=251,
        client_msg_no=PRIVATE_RECEIPT,
        payload=payload,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "platform_type,message_type",
    (("telegram", 1), ("telegram", 2), ("slack", 1), ("slack", 2), ("custom", 1)),
)
async def test_success_does_not_log_recipient_receipt_or_media_url(
    platform_type: str,
    message_type: int,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def resolve_recipient(_visitor_id: str) -> str:
        return PRIVATE_RECIPIENT

    async def send_success(**_kwargs: object) -> dict[str, bool]:
        return {"ok": True}

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def get(self, _url: str) -> SimpleNamespace:
            return SimpleNamespace(content=b"image", raise_for_status=lambda: None)

        async def post(self, _url: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(messages, "resolve_visitor_platform_open_id", resolve_recipient)
    monkeypatch.setattr(messages, "slack_send_text", send_success)
    monkeypatch.setattr(messages, "slack_send_file", send_success)
    monkeypatch.setattr(telegram_utils, "telegram_send_text", send_success)
    monkeypatch.setattr(telegram_utils, "telegram_send_photo", send_success)
    monkeypatch.setattr(messages.httpx, "AsyncClient", Client)
    caplog.set_level(logging.INFO)

    response = await messages.send_message(
        request_for(message_type),
        SimpleNamespace(state=SimpleNamespace(request_id="safe-request-id")),
        FakeDatabase(platform_type),
    )

    assert response["ok"] is True
    for marker in (
        PRIVATE_RECIPIENT,
        PRIVATE_RECEIPT,
        "private-image-marker",
        "private-url-marker",
        "private-callback-marker",
    ):
        assert marker not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_type", ("telegram", "slack"))
async def test_provider_failure_does_not_echo_provider_description(
    platform_type: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def resolve_recipient(_visitor_id: str) -> str:
        return PRIVATE_RECIPIENT

    async def send_failure(**_kwargs: object) -> dict[str, object]:
        return {
            "ok": False,
            "description": PRIVATE_PROVIDER_ERROR,
            "error": PRIVATE_PROVIDER_ERROR,
        }

    monkeypatch.setattr(messages, "resolve_visitor_platform_open_id", resolve_recipient)
    monkeypatch.setattr(messages, "slack_send_text", send_failure)
    monkeypatch.setattr(telegram_utils, "telegram_send_text", send_failure)
    caplog.set_level(logging.INFO)

    response = await messages.send_message(
        request_for(1),
        SimpleNamespace(state=SimpleNamespace(request_id="safe-request-id")),
        FakeDatabase(platform_type),
    )

    assert response.status_code == 502
    assert PRIVATE_PROVIDER_ERROR not in response.body.decode("utf-8")
    assert PRIVATE_PROVIDER_ERROR not in caplog.text
