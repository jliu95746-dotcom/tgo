"""Connection requests stay on one owned public TLS origin and bounded body."""

import httpx
import pytest

from app.domain.business_tools.target_transport import (
    PinnedBusinessTransport,
    BusinessTargetRejected,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.1.2.3"],
        ["169.254.169.254"],
        ["::1"],
        ["fe80::1"],
        ["93.184.216.34", "192.168.1.1"],
        ["224.0.0.1"],
        ["::ffff:127.0.0.1"],
        [],
    ],
)
async def test_no_request_is_sent_if_any_dns_result_is_not_public(addresses):
    calls = []

    async def resolve(_host, _port):
        return addresses

    def send(request):
        calls.append(request)
        return httpx.Response(200, json={})

    transport = PinnedBusinessTransport(
        "https://orders.example.test",
        ["/orders"],
        resolver=resolve,
        inner=httpx.MockTransport(send),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(BusinessTargetRejected):
            await client.get("https://orders.example.test/orders")
    assert calls == []


@pytest.mark.asyncio
async def test_ip_is_pinned_without_changing_host_tls_sni_or_query():
    observed = []

    async def resolve(host, port):
        assert (host, port) == ("orders.example.test", 443)
        return ["93.184.216.34"]

    def send(request):
        observed.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = PinnedBusinessTransport(
        "https://orders.example.test",
        ["/orders"],
        resolver=resolve,
        inner=httpx.MockTransport(send),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get(
            "https://orders.example.test/orders?order_no=TEST-001",
            headers={"Host": "stolen.example.test"},
        )
    assert response.json() == {"ok": True}
    assert str(observed[0].url) == "https://93.184.216.34/orders?order_no=TEST-001"
    assert observed[0].headers["host"] == "orders.example.test"
    assert observed[0].extensions["sni_hostname"] == "orders.example.test"
    assert observed[0].headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://orders.example.test/orders",
        "https://other.example.test/orders",
        "https://orders.example.test/admin",
        "https://orders.example.test:8443/orders",
    ],
)
async def test_url_scheme_origin_port_and_path_cannot_be_changed(url):
    async def resolve(_host, _port):
        raise AssertionError("must fail before DNS")

    transport = PinnedBusinessTransport(
        "https://orders.example.test",
        ["/orders"],
        resolver=resolve,
        inner=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(BusinessTargetRejected):
            await client.get(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Location": "https://other.example.test", "status": "redirect"},
        {"Content-Encoding": "gzip"},
        {"Content-Length": "9999999"},
    ],
)
async def test_redirect_compression_and_oversized_advertised_body_are_rejected(headers):
    async def resolve(_host, _port):
        return ["93.184.216.34"]

    transport = PinnedBusinessTransport(
        "https://orders.example.test",
        ["/orders"],
        resolver=resolve,
        inner=httpx.MockTransport(
            lambda _: httpx.Response(
                302 if "Location" in headers else 200, headers=headers
            )
        ),
        max_response_bytes=64,
    )
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        with pytest.raises(BusinessTargetRejected):
            await client.get("https://orders.example.test/orders")


@pytest.mark.asyncio
async def test_stream_limit_closes_upstream_without_loading_unbounded_content():
    class Chunks(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"x" * 40
            yield b"x" * 40
            raise AssertionError("do not read another chunk")

        async def aclose(self):
            self.closed = True

    stream = Chunks()

    async def resolve(_host, _port):
        return ["93.184.216.34"]

    transport = PinnedBusinessTransport(
        "https://orders.example.test",
        ["/orders"],
        resolver=resolve,
        inner=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
        max_response_bytes=64,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(BusinessTargetRejected):
            await client.get("https://orders.example.test/orders")
    assert stream.closed
