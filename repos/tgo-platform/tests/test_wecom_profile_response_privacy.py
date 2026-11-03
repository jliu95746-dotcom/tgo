"""Customer profile and bot response diagnostics must not copy private data."""

import logging

import httpx
import pytest

from app.api import wecom_utils


@pytest.mark.asyncio
async def test_profile_lookup_does_not_print_customer_data_or_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    visitor_id = "visitor-private-marker"
    nickname = "nickname-private-marker"
    avatar = "avatar-private-marker"
    secret = "secret-private-marker"

    async def token(*_args: str) -> str:
        return "access-token"

    async def batch(*_args: object) -> dict[str, dict[str, str]]:
        return {visitor_id: {"nickname": nickname, "avatar": avatar}}

    monkeypatch.setattr(wecom_utils, "wecom_get_access_token", token)
    monkeypatch.setattr(wecom_utils, "_wecom_kf_batch_get_customer_basic", batch)
    caplog.set_level(logging.DEBUG)
    assert await wecom_utils.get_wecom_visitor_profile("corp", secret, visitor_id) == {
        "nickname": nickname,
        "avatar": avatar,
    }
    assert not capsys.readouterr().out
    assert not any(
        value in caplog.text for value in (visitor_id, nickname, avatar, secret)
    )

    async def failed_batch(*_args: object) -> dict[str, dict[str, str]]:
        raise RuntimeError(f"{visitor_id} {nickname} {secret}")

    async def failed_fallback(*_args: object) -> None:
        raise RuntimeError(f"{visitor_id} {avatar} {secret}")

    monkeypatch.setattr(wecom_utils, "_wecom_kf_batch_get_customer_basic", failed_batch)
    monkeypatch.setattr(wecom_utils, "_wecom_externalcontact_get", failed_fallback)
    assert await wecom_utils.get_wecom_visitor_profile("corp", secret, visitor_id) == {
        "nickname": None,
        "avatar": None,
    }
    assert not capsys.readouterr().out
    assert not any(
        value in caplog.text for value in (visitor_id, nickname, avatar, secret)
    )


@pytest.mark.asyncio
async def test_external_contact_response_is_not_printed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={
                    "errcode": 0,
                    "external_contact": {
                        "name": "private-name",
                        "avatar": "private-avatar",
                    },
                },
            )

    monkeypatch.setattr(wecom_utils.httpx, "AsyncClient", Client)
    assert await wecom_utils._wecom_externalcontact_get(
        "private-token",
        "private-visitor",
    ) == {"name": "private-name", "avatar": "private-avatar"}
    assert not capsys.readouterr().out


@pytest.mark.asyncio
async def test_bot_diagnostics_and_provider_error_do_not_expose_reply_or_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    response_url = "https://example.invalid/reply?token=private-url-marker"
    reply = "private-reply-marker"
    provider_error = "private-provider-marker"

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: object) -> httpx.Response:
            assert url == response_url
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "errcode": 40001,
                    "errmsg": provider_error,
                },
            )

    monkeypatch.setattr(wecom_utils.httpx, "AsyncClient", Client)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError) as error:
        await wecom_utils.wecom_bot_send_response_text(response_url, reply)
    assert "40001" in str(error.value)
    assert not any(
        value in caplog.text + str(error.value)
        for value in (
            response_url,
            "private-url-marker",
            reply,
            provider_error,
        )
    )


@pytest.mark.asyncio
async def test_bot_success_keeps_receipt_and_http_failure_hides_callback_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback_url = "https://example.invalid/reply?token=private-url-marker"
    receipt = {"errcode": 0, "msgid": "receipt-id"}

    class Client:
        status = 200

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                self.status,
                request=httpx.Request("POST", url),
                json=receipt,
            )

    monkeypatch.setattr(wecom_utils.httpx, "AsyncClient", Client)
    assert (
        await wecom_utils.wecom_bot_send_response_text(callback_url, "hello") == receipt
    )
    Client.status = 503
    with pytest.raises(RuntimeError, match="HTTP 503") as error:
        await wecom_utils.wecom_bot_send_response_text(callback_url, "hello")
    assert "private-url-marker" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["batch", "upload", "kf_send", "app_send"])
async def test_provider_rejection_does_not_copy_raw_payload_into_exception(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_message = "provider-private-marker"

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "errcode": 40001,
                    "errmsg": private_message,
                },
            )

    monkeypatch.setattr(wecom_utils.httpx, "AsyncClient", Client)
    with pytest.raises(RuntimeError) as error:
        if operation == "batch":
            await wecom_utils._wecom_kf_batch_get_customer_basic("token", ["visitor"])
        elif operation == "upload":
            await wecom_utils.wecom_upload_temp_media("token", b"image")
        elif operation == "kf_send":
            await wecom_utils.wecom_kf_send_msg(
                "token",
                "kf",
                "visitor",
                "text",
                {"content": "hello"},
            )
        else:
            await wecom_utils.wecom_send_app_message(
                "token",
                "employee",
                1,
                "text",
                {"content": "hello"},
            )
    assert "40001" in str(error.value)
    assert private_message not in str(error.value)
