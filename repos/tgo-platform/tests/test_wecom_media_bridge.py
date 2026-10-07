from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.domain.entities import NormalizedMessage
from app.domain.services import dispatcher
from app.domain.services.media import wecom_bridge
from app.domain.services.media.wecom_bridge import WeComMediaBridge
from app.domain.services.listeners.wecom_listener import WeComChannelListener


@pytest.mark.asyncio
async def test_media_upload_is_scoped_to_the_registered_visitor() -> None:
    visitor_id = uuid4()
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"file_id": str(uuid4())})

    client = httpx.AsyncClient(
        base_url="http://tgo-api:8000", transport=httpx.MockTransport(respond)
    )
    bridge = WeComMediaBridge(client=client)
    try:
        media_file_id = await bridge.upload(
            platform_api_key="platform-key",
            visitor_id=visitor_id,
            media_type="image",
            mime_type="image/png",
            content=b"\x89PNG\r\n\x1a\nimage-bytes",
        )
    finally:
        await client.aclose()

    assert media_file_id is not None
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/chat/upload"
    assert requests[0].headers["X-Platform-API-Key"] == "platform-key"
    body = requests[0].content
    assert str(visitor_id).encode() + b"-vtr" in body
    assert b'name="channel_type"' in body and b"251" in body
    assert b"image/png" in body


@pytest.mark.asyncio
async def test_wecom_media_request_keeps_file_and_source_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[object] = []

    class FakeClient:
        def chat_completion(self, request: object):
            requests.append(request)

            async def empty_frames():
                if False:
                    yield b""

            return empty_frames()

    class FakeEvents:
        async def stream_events(self, _frames: object):
            yield type("Event", (), {"event": "human_handoff", "payload": {"event_type": "human_handoff"}})()

    class FakeSession:
        async def scalar(self, _query: object):
            return type("Platform", (), {"type": "wecom", "config": {}})()

    class FakeAdapter:
        supports_stream = False

    async def fake_adapter(*_args: object, **_kwargs: object) -> FakeAdapter:
        return FakeAdapter()

    monkeypatch.setattr(dispatcher, "select_adapter_for_target", fake_adapter)
    message = NormalizedMessage(
        source="wecom", from_uid="customer", content="/v1/chat/files/" + str(uuid4()),
        platform_api_key="platform-key", platform_type="wecom",
        platform_id=str(uuid4()),
        extra={"msg_type": 2, "message_id": "source-message", "media_file_id": str(uuid4())},
    )
    await dispatcher.process_message(message, FakeSession(), FakeClient(), FakeEvents())
    request = requests[0]
    assert request.msg_type == 2
    assert request.media_file_id == message.extra["media_file_id"]
    assert request.source_message_id == "source-message"
    assert request.extra == {"message_id": "source-message"}


@pytest.mark.asyncio
async def test_downloaded_media_reuses_uploaded_file_after_retry() -> None:
    content = b"\x89PNG\r\n\x1a\nimage-bytes"
    platform_id = uuid4()
    inbox_id = uuid4()
    visitor_id = uuid4()
    uploaded_file_id = uuid4()
    media = SimpleNamespace(
        object_key="wecom/media.enc", sha256=hashlib.sha256(content).hexdigest(),
        media_type="image", mime_type="image/png",
    )

    class FakeSession:
        commits = 0

        async def scalar(self, _query: object) -> object:
            return media

        async def commit(self) -> None:
            self.commits += 1

    class FakeStorage:
        reads = 0

        async def get(self, *, object_key: str) -> bytes:
            assert object_key == "wecom/media.enc"
            self.reads += 1
            return content

    class FakeBridge:
        uploads = 0

        async def upload(self, **kwargs: object):
            assert kwargs["visitor_id"] == visitor_id
            self.uploads += 1
            return uploaded_file_id

    session = FakeSession()
    storage = FakeStorage()
    bridge = FakeBridge()
    listener = object.__new__(WeComChannelListener)
    listener._media_storage = storage
    listener._media_bridge = bridge
    record = SimpleNamespace(id=inbox_id, raw_payload={"kf_sync_msg": {}})
    platform = SimpleNamespace(id=platform_id, api_key="platform-key")
    first = {"extra": {}}
    await listener._prepare_media_message(
        session, platform, record, visitor_id, first,
    )
    second = {"extra": {}}
    await listener._prepare_media_message(
        session, platform, record, visitor_id, second,
    )

    assert session.commits == 1
    assert bridge.uploads == storage.reads == 1
    assert first["content"] == second["content"] == f"/v1/chat/files/{uploaded_file_id}"
    assert first["extra"]["media_file_id"] == str(uploaded_file_id)


@pytest.mark.asyncio
async def test_amr_is_converted_before_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ffmpeg(_content: bytes, *_args: str) -> bytes:
        return b"RIFF\x00\x00\x00\x00WAVEvoice"

    monkeypatch.setattr(wecom_bridge, "_run_ffmpeg", fake_ffmpeg)
    prepared = await wecom_bridge.prepare_media(b"#!AMR\nvoice", "audio/amr")
    assert prepared.mime_type == "audio/wav"
    assert prepared.filename.endswith(".wav")
    assert prepared.content.startswith(b"RIFF")
