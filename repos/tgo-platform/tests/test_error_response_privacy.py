"""Unexpected platform failures never return exception text to callers."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1 import internal
from app.api.v1 import messages
from app.api.error_utils import (
    generic_exception_handler,
    sqlalchemy_exception_handler,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler,error",
    (
        (generic_exception_handler, RuntimeError("private@example.com")),
        (sqlalchemy_exception_handler, SQLAlchemyError("private@example.com")),
    ),
)
async def test_unexpected_error_hides_private_exception_message(
    handler, error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    request = SimpleNamespace(state=SimpleNamespace(request_id="safe-request-id"))
    response = await handler(request, error)
    payload = json.loads(response.body)
    assert response.status_code == 500
    assert payload["request_id"] == "safe-request-id"
    assert payload["error"]["details"] is None
    assert "private@example.com" not in response.body.decode("utf-8")
    assert "private@example.com" not in caplog.text


@pytest.mark.asyncio
async def test_untrusted_request_id_is_not_copied_into_server_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = SimpleNamespace(state=SimpleNamespace(request_id="private@example.com"))
    await generic_exception_handler(request, RuntimeError("server failure"))
    assert "private@example.com" not in caplog.text


@pytest.mark.asyncio
async def test_send_message_failure_does_not_echo_or_log_private_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail_recipient(_visitor_id: str) -> str:
        raise RuntimeError("private@example.com")

    class FakeDatabase:
        async def scalar(self, _query: object) -> SimpleNamespace:
            return SimpleNamespace(type="feishu_bot", config={})

    monkeypatch.setattr(messages, "resolve_visitor_platform_open_id", fail_recipient)
    payload = messages.SendMessageRequest(
        platform_api_key="fixture-key",
        from_uid="staff",
        channel_id="owned-visitor-vtr",
        channel_type=251,
        payload={"type": 1, "content": "测试"},
    )
    request = SimpleNamespace(state=SimpleNamespace(request_id="safe-request-id"))
    response = await messages.send_message(payload, request, FakeDatabase())
    assert response.status_code == 500
    assert "private@example.com" not in response.body.decode("utf-8")
    assert "private@example.com" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ("reload", "stop"))
async def test_internal_slack_failure_hides_private_exception(
    action: str, caplog: pytest.LogCaptureFixture
) -> None:
    class FailingListener:
        async def reload_platform_with_config(self, **_kwargs: object) -> bool:
            raise RuntimeError("private@example.com")

        async def stop_platform(self, _platform_id: str) -> bool:
            raise RuntimeError("private@example.com")

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(slack_listener=FailingListener()))
    )
    with pytest.raises(internal.HTTPException) as caught:
        if action == "reload":
            await internal.reload_slack_platform(
                internal.SlackPlatformConfig(
                    platform_id="fixture-platform",
                    project_id="fixture-project",
                    bot_token="fixture-token",
                    app_token="fixture-app-token",
                ),
                request,
            )
        else:
            await internal.stop_slack_platform("fixture-platform", request)

    assert caught.value.status_code == 500
    assert "private@example.com" not in str(caught.value.detail)
    assert "private@example.com" not in caplog.text


@pytest.mark.asyncio
async def test_internal_inbound_failure_hides_private_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def fail_processing(*_args: object) -> None:
        raise RuntimeError("private@example.com")

    class FakeDatabase:
        async def scalar(self, _query: object) -> SimpleNamespace:
            return SimpleNamespace(type="douyin_personal", api_key="fixture-key")

    monkeypatch.setattr(internal, "process_message", fail_processing)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(tgo_api_client=object(), sse_manager=object())
        )
    )
    inbound = internal.VisionAgentInboundMessage(
        platform_id=str(uuid4()),
        platform_type="douyin_personal",
        from_uid="fixture-visitor",
        content="测试",
    )
    with pytest.raises(internal.HTTPException) as caught:
        await internal.vision_agent_inbound(inbound, request, FakeDatabase())

    assert caught.value.status_code == 500
    assert "private@example.com" not in str(caught.value.detail)
    assert "private@example.com" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_type", ("telegram", "slack"))
async def test_image_download_failure_hides_private_exception(
    platform_type: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def resolve_recipient(_visitor_id: str) -> str:
        return "fixture-recipient"

    class FailingClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FailingClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def get(self, _url: str) -> None:
            raise RuntimeError("private@example.com")

    class FakeDatabase:
        async def scalar(self, _query: object) -> SimpleNamespace:
            return SimpleNamespace(
                type=platform_type, config={"bot_token": "fixture-token"}
            )

    monkeypatch.setattr(messages, "resolve_visitor_platform_open_id", resolve_recipient)
    monkeypatch.setattr(messages.httpx, "AsyncClient", FailingClient)
    payload = messages.SendMessageRequest(
        platform_api_key="fixture-key",
        from_uid="staff",
        channel_id="owned-visitor-vtr",
        channel_type=251,
        payload={"type": 2, "url": "https://safe.example.test/image.png"},
    )
    request = SimpleNamespace(state=SimpleNamespace(request_id="safe-request-id"))
    response = await messages.send_message(payload, request, FakeDatabase())

    assert response.status_code == 400
    assert "private@example.com" not in response.body.decode("utf-8")
    assert "private@example.com" not in caplog.text
