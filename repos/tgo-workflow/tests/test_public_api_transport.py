"""Generic workflow queries stay on one verified public HTTPS target."""

import logging

import httpx
import pytest

from app.integrations.public_http import (
    PinnedPublicGetTransport,
    PublicTargetRejected,
    bounded_api_get,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://orders.example.test/query",
        "https://user:secret@orders.example.test/query",
        "https://orders.example.test:8443/query",
        "https://orders.example.test/query#ignored",
    ],
)
def test_transport_rejects_untrusted_configured_origin(url):
    with pytest.raises(PublicTargetRejected):
        PinnedPublicGetTransport(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [
        [],
        ["127.0.0.1"],
        ["10.2.3.4"],
        ["169.254.169.254"],
        ["::1"],
        ["::ffff:127.0.0.1"],
        ["93.184.216.34", "192.168.1.1"],
    ],
)
async def test_private_or_mixed_dns_is_rejected_before_send(addresses):
    sent = []

    async def resolve(host, port):
        assert (host, port) == ("orders.example.test", 443)
        return addresses

    transport = PinnedPublicGetTransport(
        "https://orders.example.test/query",
        resolver=resolve,
        inner=httpx.MockTransport(
            lambda request: sent.append(request) or httpx.Response(200)
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PublicTargetRejected):
            await client.get("https://orders.example.test/query")
    assert not sent


@pytest.mark.asyncio
async def test_query_pins_verified_ip_and_preserves_tls_host_and_parameters():
    sent = []

    async def resolve(host, port):
        assert (host, port) == ("orders.example.test", 443)
        return ["93.184.216.34"]

    response = await bounded_api_get(
        "https://orders.example.test/query",
        {"Host": "wrong.example.test"},
        {"order_no": "LY-123"},
        resolver=resolve,
        inner=httpx.MockTransport(
            lambda request: sent.append(request)
            or httpx.Response(200, json={"ok": True})
        ),
    )
    assert response.json() == {"ok": True}
    assert str(sent[0].url) == "https://93.184.216.34/query?order_no=LY-123"
    assert sent[0].headers["host"] == "orders.example.test"
    assert sent[0].headers["accept-encoding"] == "identity"
    assert sent[0].extensions["sni_hostname"] == "orders.example.test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://orders.example.test/query",
        "https://other.example.test/query",
        "https://orders.example.test/admin",
        "https://orders.example.test:8443/query",
    ],
)
async def test_transport_rejects_changed_scheme_host_port_or_path(url):
    async def resolve(_host, _port):
        raise AssertionError("unexpected DNS lookup")

    transport = PinnedPublicGetTransport(
        "https://orders.example.test/query",
        resolver=resolve,
        inner=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PublicTargetRejected):
            await client.get(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,headers",
    [
        (302, {"Location": "https://other.example.test"}),
        (200, {"Content-Encoding": "gzip"}),
        (200, {"Content-Length": "9999999"}),
        (200, {"Content-Length": "invalid"}),
    ],
)
async def test_redirect_encoding_and_invalid_length_fail_closed(status, headers):
    async def resolve(_host, _port):
        return ["93.184.216.34"]

    with pytest.raises(PublicTargetRejected):
        await bounded_api_get(
            "https://orders.example.test/query",
            {},
            {},
            resolver=resolve,
            inner=httpx.MockTransport(
                lambda _: httpx.Response(status, headers=headers)
            ),
            max_response_bytes=64,
        )


@pytest.mark.asyncio
async def test_stream_limit_closes_upstream_before_next_chunk():
    class Chunks(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"x" * 40
            yield b"y" * 40
            raise AssertionError("response must be stopped at limit")

        async def aclose(self):
            self.closed = True

    stream = Chunks()

    async def resolve(_host, _port):
        return ["93.184.216.34"]

    with pytest.raises(PublicTargetRejected):
        await bounded_api_get(
            "https://orders.example.test/query",
            {},
            {},
            resolver=resolve,
            inner=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
            max_response_bytes=64,
        )
    assert stream.closed


@pytest.mark.asyncio
async def test_network_error_and_http_log_do_not_expose_customer_query(caplog):
    private_order = "PRIVATE-ORDER-123"

    async def resolve(_host, _port):
        return ["93.184.216.34"]

    def broken(request):
        raise httpx.ConnectError(f"connection to {request.url} failed", request=request)

    with caplog.at_level(logging.INFO, logger="httpx"):
        with pytest.raises(RuntimeError, match="API 查询失败") as error:
            await bounded_api_get(
                "https://orders.example.test/query",
                {},
                {"order_no": private_order},
                resolver=resolve,
                inner=httpx.MockTransport(broken),
            )
    assert private_order not in str(error.value)
    assert private_order not in caplog.text


@pytest.mark.asyncio
async def test_successful_query_http_log_redacts_customer_query(caplog):
    async def resolve(_host, _port):
        return ["93.184.216.34"]

    with caplog.at_level(logging.INFO, logger="httpx"):
        await bounded_api_get(
            "https://orders.example.test/query",
            {},
            {"order_no": "PRIVATE-ORDER"},
            resolver=resolve,
            inner=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})),
        )
    assert "PRIVATE-ORDER" not in caplog.text
