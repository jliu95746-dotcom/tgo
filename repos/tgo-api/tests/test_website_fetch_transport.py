"""The public-page transport pins destinations and closes bounded streams."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import website_fetch as service


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, *, error=None, delay=0):
        self.chunks = chunks
        self.closed = False
        self.error = error
        self.delay = delay

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.closed = True


@pytest.fixture
def remote():
    state = SimpleNamespace(
        resolver=AsyncMock(return_value=["1.1.1.1"]),
        requests=[],
        response=httpx.Response(200, text="ok"),
    )

    def handle(request):
        state.requests.append(request)
        return state.response

    state.transport = service.PublicWebsiteTransport(
        resolver=state.resolver,
        inner=httpx.MockTransport(handle),
        max_response_bytes=8,
    )
    return state


async def read(
    remote, url="https://site.example.invalid:8443/page?a=1", method="GET"
):
    async with httpx.AsyncClient(
        transport=remote.transport, trust_env=False
    ) as client:
        return await client.request(method, url)


@pytest.mark.asyncio
async def test_public_custom_port_and_stream_are_preserved(remote):
    stream = Chunks([b"1234", b"5678"])
    remote.response = httpx.Response(200, stream=stream)
    response = await read(remote)
    assert response.content == b"12345678" and stream.closed
    request = remote.requests[0]
    assert request.url.host == "1.1.1.1" and request.url.port == 8443
    assert request.headers["host"] == "site.example.invalid:8443"
    assert request.extensions["sni_hostname"] == "site.example.invalid"
    remote.resolver.assert_awaited_once_with("site.example.invalid", 8443)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,chunks",
    [
        ({"content-length": "9"}, [b"short"]),
        ({"content-length": "invalid"}, [b"short"]),
        ({"content-length": "-1"}, [b"short"]),
        ({"content-length": "1"}, [b"12345", b"6789"]),
        ({}, [b"123456789"]),
        ({"content-encoding": "gzip"}, [b"compressed"]),
    ],
)
async def test_rejected_responses_close_streams(remote, headers, chunks):
    stream = Chunks(chunks)
    remote.response = httpx.Response(200, headers=headers, stream=stream)
    with pytest.raises(service.WebsiteResponseRejected):
        await read(remote)
    assert stream.closed
    assert len(remote.requests) == 1


@pytest.mark.asyncio
async def test_consumed_body_is_still_bounded(remote):
    remote.response = httpx.Response(
        200, content=b"123456789", headers={"content-length": "1"}
    )
    with pytest.raises(service.WebsiteResponseRejected):
        await read(remote)


@pytest.mark.asyncio
async def test_midstream_failure_also_closes_response(remote):
    stream = Chunks(
        [b"part"], error=httpx.ReadError("synthetic-private-error")
    )
    remote.response = httpx.Response(200, stream=stream)
    with pytest.raises(httpx.ReadError):
        await read(remote)
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,method",
    [
        ("https://site.example.invalid/page", "POST"),
        ("file:///private", "GET"),
        ("https://user:password@site.example.invalid/page", "GET"),
    ],
)
async def test_invalid_request_never_resolves_or_connects(remote, url, method):
    with pytest.raises(service.WebsiteTargetRejected):
        await read(remote, url, method)
    remote.resolver.assert_not_awaited()
    assert not remote.requests


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1",
        "10.0.0.1",
        "0.0.0.0",
        "169.254.169.254",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1%lo",
        "::ffff:10.0.0.1",
        "2002:a00:1::",
        "not-an-ip",
    ],
)
def test_private_embedded_or_scoped_addresses_are_not_public(value):
    assert not service._public_address(value)


@pytest.mark.parametrize(
    "value", ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"]
)
def test_public_addresses_are_supported(value):
    assert service._public_address(value)


def test_nonpositive_limit_is_invalid():
    with pytest.raises(ValueError):
        service.PublicWebsiteTransport(max_response_bytes=0)


@pytest.mark.asyncio
async def test_literal_ip_does_not_need_dns():
    assert await service.public_addresses("1.1.1.1", 443) == ["1.1.1.1"]


@pytest.mark.asyncio
async def test_dns_answers_are_deduplicated_without_contacting_network(
    monkeypatch,
):
    resolver = AsyncMock(
        return_value=[
            (2, 1, 6, "", ("1.1.1.1", 443)),
            (2, 1, 6, "", ("1.1.1.1", 443)),
            (10, 1, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
        ]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    assert await service.public_addresses("site.example.invalid", 443) == [
        "1.1.1.1",
        "2606:4700:4700::1111",
    ]
