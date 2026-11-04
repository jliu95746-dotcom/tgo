"""Channel diagnostics must not copy message bodies, provider data or secrets."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from slack_sdk.errors import SlackApiError

from app.api import feishu_utils, slack_utils, telegram_utils
from app.domain.entities import StreamEvent
from app.domain.services.adapters.base import SimpleStdoutAdapter
from app.domain.services.adapters import slack as slack_adapter
from app.domain.services.adapters import telegram as telegram_adapter
from app.domain.services.listeners import slack_listener, telegram_listener


PRIVATE = "private-customer-marker"
SECRET = "private-credential-marker"


def assert_private_values_absent(caplog, capsys) -> None:
    output = capsys.readouterr()
    combined = caplog.text + output.out + output.err
    assert PRIVATE not in combined
    assert SECRET not in combined


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ("send_text", "download_file"))
@pytest.mark.parametrize("failure", ("http_status", "transport"))
async def test_telegram_http_exception_omits_token_and_original_request(
    action: str,
    failure: str,
    monkeypatch,
) -> None:
    private_url = f"https://api.telegram.org/bot{SECRET}/sendMessage"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return self.fail("POST")

        async def get(self, *_args, **_kwargs):
            return self.fail("GET")

        def fail(self, method):
            request = httpx.Request(method, private_url)
            if failure == "transport":
                raise httpx.ConnectError(SECRET, request=request)
            return httpx.Response(401, request=request)

    monkeypatch.setattr(telegram_utils.httpx, "AsyncClient", lambda **_kwargs: Client())
    with pytest.raises(httpx.HTTPError) as caught:
        if action == "send_text":
            await telegram_utils.telegram_send_text(SECRET, PRIVATE, "reply")
        else:
            await telegram_utils.telegram_download_file(SECRET, PRIVATE)

    error = caught.value
    assert SECRET not in str(error)
    assert PRIVATE not in str(error)
    assert SECRET not in str(error.request.url)
    assert error.__context__ is None
    if failure == "http_status":
        assert isinstance(error, httpx.HTTPStatusError)
        assert error.response.status_code == 401
        assert SECRET not in str(error.response.request.url)
    else:
        assert isinstance(error, httpx.RequestError)


@pytest.mark.asyncio
async def test_outbound_adapter_diagnostics_do_not_expose_results(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    async def telegram_send(**_kwargs):
        return {"provider_result": PRIVATE}

    async def slack_send(**_kwargs):
        return {"provider_result": PRIVATE}

    monkeypatch.setattr(telegram_adapter, "telegram_send_text", telegram_send)
    monkeypatch.setattr(slack_adapter, "slack_send_text", slack_send)
    caplog.set_level(logging.DEBUG)

    simple = SimpleStdoutAdapter()
    await simple.send_incremental(StreamEvent(event="delta", payload={"text": PRIVATE}))
    await simple.send_final({"text": PRIVATE})
    await telegram_adapter.TelegramAdapter(SECRET, "private-chat").send_final(
        {"text": PRIVATE}
    )
    await slack_adapter.SlackAdapter(SECRET, "private-channel").send_final(
        {"text": PRIVATE}
    )

    assert_private_values_absent(caplog, capsys)


@pytest.mark.asyncio
async def test_feishu_profile_diagnostics_do_not_expose_secret_or_person(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    async def token(*_args, **_kwargs):
        return SECRET

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"code": 0, "data": {"user": {"name": PRIVATE}}},
            )

    monkeypatch.setattr(feishu_utils, "feishu_get_tenant_access_token", token)
    monkeypatch.setattr(feishu_utils.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.DEBUG)
    assert await feishu_utils.feishu_get_user_info("app-id", SECRET, PRIVATE) == (
        PRIVATE,
        None,
    )
    assert_private_values_absent(caplog, capsys)


@pytest.mark.asyncio
async def test_feishu_token_error_does_not_include_provider_body(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"code": 1, "msg": SECRET},
            )

    monkeypatch.setattr(feishu_utils, "get_redis_client", AsyncMock(return_value=None))
    monkeypatch.setattr(feishu_utils.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError) as error:
        await feishu_utils.feishu_get_tenant_access_token("new-private-app", SECRET)
    assert SECRET not in str(error.value)
    assert_private_values_absent(caplog, capsys)


@pytest.mark.asyncio
async def test_telegram_webhook_diagnostics_do_not_expose_url_or_provider_body(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"ok": False, "description": PRIVATE},
            )

    monkeypatch.setattr(telegram_utils.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.DEBUG)
    result = await telegram_utils.telegram_set_webhook(
        SECRET, f"https://example.test/hook/{SECRET}"
    )
    assert result["ok"] is False
    assert_private_values_absent(caplog, capsys)


@pytest.mark.asyncio
async def test_slack_api_success_does_not_log_channel(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    class Client:
        def __init__(self, **_kwargs):
            pass

        def chat_postMessage(self, **_kwargs):
            return SimpleNamespace(data={"ok": True})

    monkeypatch.setattr(slack_utils, "WebClient", Client)
    caplog.set_level(logging.DEBUG)
    assert await slack_utils.slack_send_text(SECRET, PRIVATE, "reply") == {"ok": True}
    assert_private_values_absent(caplog, capsys)


@pytest.mark.asyncio
async def test_slack_provider_error_does_not_log_provider_body(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    class Client:
        def __init__(self, **_kwargs):
            pass

        def chat_postMessage(self, **_kwargs):
            raise SlackApiError("failed", {"error": PRIVATE})

    monkeypatch.setattr(slack_utils, "WebClient", Client)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SlackApiError):
        await slack_utils.slack_send_text(SECRET, "private-channel", "reply")
    assert_private_values_absent(caplog, capsys)


@pytest.mark.asyncio
async def test_telegram_send_error_does_not_log_provider_body(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"ok": False, "description": PRIVATE},
            )

    monkeypatch.setattr(telegram_utils.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.DEBUG)
    result = await telegram_utils.telegram_send_text(SECRET, "private-chat", "reply")
    assert result["ok"] is False
    assert_private_values_absent(caplog, capsys)


@pytest.mark.asyncio
async def test_feishu_reply_error_does_not_include_provider_body(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"code": 1, "msg": PRIVATE},
            )

    monkeypatch.setattr(feishu_utils.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError) as error:
        await feishu_utils.feishu_reply_message(
            SECRET, PRIVATE, "text", {"text": PRIVATE}
        )
    assert PRIVATE not in str(error.value)
    assert_private_values_absent(caplog, capsys)


def test_slack_listener_diagnostics_do_not_expose_customer_or_response(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    class Response:
        def __init__(self, status_code, data):
            self.status_code = status_code
            self._data = data

        def json(self):
            return self._data

    def post(url, **_kwargs):
        if url.endswith("/v1/chat/completion"):
            return Response(200, {"message": PRIVATE, "success": True})
        return Response(201, {"channel_id": "private-channel"})

    class Client:
        def users_info(self, **_kwargs):
            return {"user": {"profile": {"display_name": PRIVATE}}}

        def chat_postMessage(self, **_kwargs):
            return {"ok": True}

    monkeypatch.setattr(slack_listener.requests, "post", post)
    monkeypatch.setattr(slack_listener, "channel_registration_headers", lambda: {})
    caplog.set_level(logging.DEBUG)
    platform = slack_listener._SlackPlatformEntry(
        id=uuid4(),
        project_id=uuid4(),
        api_key=SECRET,
        cfg=slack_listener.SlackPlatformConfig(bot_token=SECRET),
    )
    listener = slack_listener.SlackChannelListener.__new__(
        slack_listener.SlackChannelListener
    )
    listener._process_slack_message_sync(
        platform,
        {
            "user": "private-user",
            "channel": "private-channel",
            "ts": "1",
            "text": PRIVATE,
        },
        Client(),
    )
    assert_private_values_absent(caplog, capsys)


@pytest.mark.asyncio
async def test_telegram_listener_failure_diagnostics_do_not_expose_message(
    caplog,
    capsys,
) -> None:
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def rollback(self):
            return None

    listener = telegram_listener.TelegramChannelListener.__new__(
        telegram_listener.TelegramChannelListener
    )
    listener._session_factory = lambda: Session()
    listener._extract_message_data = lambda _update: {
        "from_display_name": PRIVATE,
        "chat_type": "private",
        "content": PRIVATE,
        "msg_type": 1,
    }
    listener._get_or_register_visitor = AsyncMock(return_value=(None, None, None))
    listener._store_to_inbox = AsyncMock(side_effect=RuntimeError(PRIVATE))
    platform = telegram_listener._PlatformEntry(
        id=uuid4(),
        project_id=uuid4(),
        api_key=SECRET,
        cfg=telegram_listener.TelegramPlatformConfig(bot_token=SECRET),
    )
    caplog.set_level(logging.DEBUG)
    await listener._process_update(platform, {})
    assert_private_values_absent(caplog, capsys)
