"""DingTalk send diagnostics must not disclose webhook tokens or provider data."""

from __future__ import annotations

import logging

import httpx
import pytest

from app.api import dingtalk_utils


SECRET = "fixture-webhook-secret-marker"
PRIVATE = "fixture-provider-private-marker"
WEBHOOK = f"https://oapi.example.test/robot/send?access_token={SECRET}"


@pytest.mark.asyncio
@pytest.mark.parametrize("errcode", (0, 40001))
async def test_provider_response_stays_out_of_logs_and_errors(
    errcode: int,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url: str, **_kwargs):
            return httpx.Response(
                200,
                json={"errcode": errcode, "errmsg": PRIVATE},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(dingtalk_utils.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.INFO)

    if errcode:
        with pytest.raises(RuntimeError) as error:
            await dingtalk_utils.dingtalk_send_text(WEBHOOK, PRIVATE)
        assert PRIVATE not in str(error.value)
        assert SECRET not in str(error.value)
    else:
        assert (await dingtalk_utils.dingtalk_send_text(WEBHOOK, PRIVATE))[
            "errcode"
        ] == 0
    assert PRIVATE not in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_direct_webhook_does_not_log_message_type(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url: str, **_kwargs):
            return httpx.Response(
                200, json={"errcode": 0}, request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(dingtalk_utils.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.INFO)

    await dingtalk_utils.dingtalk_send_webhook(WEBHOOK, PRIVATE, {"content": PRIVATE})
    assert PRIVATE not in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("http_status", "transport"))
async def test_http_error_omits_signed_webhook(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url: str, **_kwargs):
            request = httpx.Request("POST", url)
            if failure == "transport":
                raise httpx.ConnectError(SECRET, request=request)
            return httpx.Response(403, request=request)

    monkeypatch.setattr(dingtalk_utils.httpx, "AsyncClient", lambda **_kwargs: Client())

    with pytest.raises(httpx.HTTPError) as caught:
        await dingtalk_utils.dingtalk_send_text(WEBHOOK, PRIVATE)

    error = caught.value
    assert SECRET not in str(error)
    assert SECRET not in str(error.request.url)
    assert error.__context__ is None
    if failure == "http_status":
        assert isinstance(error, httpx.HTTPStatusError)
        assert error.response.status_code == 403
    else:
        assert isinstance(error, httpx.RequestError)
