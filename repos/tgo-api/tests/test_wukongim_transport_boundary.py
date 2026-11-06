"""Untrusted IM responses cannot become receipts or leak through errors."""

import base64
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from fastapi import HTTPException
import httpx
import pytest

from app.services.wukongim_client import WuKongIMClient

PRIVATE = "synthetic-private-im-response"


@asynccontextmanager
async def connection(response=None, error=None):
    async def handle(request):
        if error is not None:
            raise error
        return response

    client = WuKongIMClient()
    client.enabled = True
    client.base_url = "http://im.fixture.invalid"
    client._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        trust_env=False,
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "body", [b"<html>bad</html>", b"null", b"true", b"7", b"[1]", b'"text"']
)
async def test_invalid_success_body_is_never_a_success_receipt(body, strict):
    async with connection(httpx.Response(200, content=body)) as client:
        with pytest.raises(HTTPException) as failure:
            await client._make_request(
                "POST",
                "/message/send",
                strict_response=strict,
            )
    assert failure.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 429, 503])
@pytest.mark.parametrize("body", [PRIVATE, {"msg": PRIVATE}])
async def test_upstream_error_body_is_not_exposed_or_logged(
    status, body, caplog
):
    async with connection(httpx.Response(status, json=body)) as client:
        with pytest.raises(HTTPException) as failure:
            await client._make_request("POST", "/message/send")
    assert failure.value.status_code == 500
    assert PRIVATE not in str(failure.value.detail)
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
async def test_transport_errors_log_only_error_class(error_type, caplog):
    async with connection(error=error_type(PRIVATE)) as client:
        with pytest.raises(HTTPException) as failure:
            await client._make_request("GET", "/route")
    assert failure.value.status_code == 500
    assert PRIVATE not in str(failure.value.detail)
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{}, {"status": 200}, [{"uid": "staff", "online": 1}]]
)
async def test_transport_preserves_valid_object_and_array_responses(body):
    async with connection(httpx.Response(200, json=body)) as client:
        assert await client._make_request("POST", "/synthetic") == body


@pytest.mark.asyncio
async def test_empty_success_and_explicit_not_found_keep_existing_behavior():
    async with connection(httpx.Response(200, content=b"")) as client:
        assert await client._make_request("POST", "/channel") == {}
    async with connection(
        httpx.Response(404, json={"msg": PRIVATE})
    ) as client:
        assert (
            await client._make_request(
                "POST",
                "/message",
                allow_not_found=True,
                strict_response=True,
            )
            == {}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, -1, "12", {}, [], None, 1.5])
async def test_invalid_sequence_is_not_returned_as_an_integer(value):
    async with connection(
        httpx.Response(200, json={"message_seq": value})
    ) as client:
        assert (
            await client.get_channel_max_message_seq(
                channel_id="fixture",
                channel_type=251,
                login_uid="staff",
            )
            is None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, 12])
async def test_valid_sequence_is_preserved(value):
    async with connection(
        httpx.Response(200, json={"message_seq": value})
    ) as client:
        assert (
            await client.get_channel_max_message_seq(
                channel_id="fixture",
                channel_type=251,
                login_uid="staff",
            )
            == value
        )


@pytest.mark.asyncio
async def test_array_response_cannot_be_used_as_send_receipt():
    async with connection(httpx.Response(200, json=[{"data": {}}])) as client:
        with pytest.raises(HTTPException) as failure:
            await client.send_message(
                payload={"type": 1, "content": "fixture"},
                channel_id="fixture",
                channel_type=251,
            )
    assert failure.value.status_code == 502


@pytest.mark.asyncio
async def test_online_status_still_uses_an_array_request_and_response():
    body = [
        {"uid": "staff-a", "online": 1, "device_flag": 1},
        {"uid": "staff-b", "online": 0, "device_flag": 1},
    ]
    async with connection(httpx.Response(200, json=body)) as client:
        request = AsyncMock(wraps=client._http_client.request)
        client._http_client.request = request
        assert await client.check_user_online_status(
            ["staff-a", "staff-b"]
        ) == ["staff-a"]
        assert request.call_args.kwargs["json"] == ["staff-a", "staff-b"]


@pytest.mark.asyncio
async def test_nested_validation_does_not_log_message_content(caplog):
    body = [{"channel_id": "fixture", "channel_type": PRIVATE}]
    async with connection(httpx.Response(200, json=body)) as client:
        with pytest.raises(ValueError):
            await client.sync_conversations("staff")
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
async def test_disabled_transport_never_opens_a_connection():
    async with connection(error=AssertionError("must not send")) as client:
        client.enabled = False
        assert await client._make_request("POST", "/message/send") == {}


def test_double_encoded_message_payload_keeps_legacy_normalization():
    payload = base64.b64encode(b'{"type":1,"content":"fixture"}').decode()
    payload = base64.b64encode(('"' + payload + '"').encode()).decode()
    assert WuKongIMClient()._decode_message_payload(payload) == {
        "type": 1,
        "content": "fixture",
    }
