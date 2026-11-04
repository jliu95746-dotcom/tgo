"""Public WeCom callbacks reject oversized and invalid bodies before storage."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.callbacks import (
    MAX_WECOM_CALLBACK_BYTES,
    _handle_wecom_webhook,
    compute_msg_signature,
)


class ChunkedRequest:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.consumed = 0
        self.query_params = {
            "msg_signature": compute_msg_signature(
                "callback-token", "1710000000", "nonce"
            ),
            "timestamp": "1710000000",
            "nonce": "nonce",
        }
        self.state = SimpleNamespace(request_id="test-request")

    async def stream(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def body(self) -> bytes:
        raise AssertionError("callback must not buffer the entire body")


class NoStorage:
    def add(self, value: object) -> None:
        raise AssertionError("rejected callback must not reach storage")


def _platform() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), config={"token": "callback-token"})


@pytest.mark.asyncio
async def test_large_wecom_body_is_rejected_without_buffering_or_storage() -> None:
    request = ChunkedRequest((b"x" * (MAX_WECOM_CALLBACK_BYTES + 1), b"unread"))
    response = await _handle_wecom_webhook(_platform(), request, NoStorage())
    assert response.status_code == 413
    assert request.consumed == 1


@pytest.mark.asyncio
async def test_invalid_utf8_wecom_body_is_a_client_error() -> None:
    response = await _handle_wecom_webhook(
        _platform(), ChunkedRequest((b"\xff\xfe",)), NoStorage()
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_small_signed_wecom_event_still_succeeds() -> None:
    request = ChunkedRequest(
        (
            b"<xml><MsgType>event</MsgType><Event>unknown</Event>",
            b"</xml>",
        )
    )
    result = await _handle_wecom_webhook(_platform(), request, NoStorage())
    assert result == {"ok": True}
    assert request.consumed == 2
