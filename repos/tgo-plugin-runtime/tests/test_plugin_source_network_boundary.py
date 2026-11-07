"""Untrusted plugin URLs must not reach private hosts or unbounded bodies."""

import asyncio
import logging
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import url_resolver
from app.services import plugin_source_transport
from app.main import app as runtime_app

DOCUMENT = b"id: fixture\nname: Fixture\nversion: '1'\n"
PRIVATE = "synthetic-source-network-private-351"


def setup_network(monkeypatch, respond, addresses=None):
    calls = []
    resolve = AsyncMock(return_value=addresses or ["93.184.216.34"])
    if addresses == []:
        resolve.return_value = []

    async def send(request):
        calls.append(request)
        result = respond(request)
        return await result if asyncio.iscoroutine(result) else result

    monkeypatch.setattr(
        url_resolver,
        "public_addresses",
        resolve,
        raising=False,
    )
    monkeypatch.setattr(
        url_resolver.httpx,
        "AsyncHTTPTransport",
        lambda **kwargs: httpx.MockTransport(send),
    )
    return calls, resolve


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["0.0.0.0"],
        ["10.2.3.4"],
        ["172.16.0.1"],
        ["192.168.1.2"],
        ["169.254.169.254"],
        ["::1"],
        ["fe80::1"],
        ["::ffff:127.0.0.1"],
        ["224.0.0.1"],
        ["100.64.0.1"],
        ["93.184.216.34", "10.0.0.1"],
        [],
        ["not-an-address"],
        ["93.184.216.34"] * 33,
    ],
)
async def test_reject_any_non_public_dns_result_before_connecting(
    monkeypatch,
    addresses,
):
    calls, resolve = setup_network(
        monkeypatch,
        lambda _: httpx.Response(200, content=DOCUMENT),
        addresses,
    )
    assert (
        await url_resolver.PluginURLResolver()._try_fetch_yaml(
            "https://source.test/plugin.yml",
        )
        is None
    )
    resolve.assert_awaited_once_with("source.test", 443)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///private/plugin.yml",
        "ftp://source.test/plugin.yml",
        f"https://user:{PRIVATE}@source.test/plugin.yml",
    ],
)
async def test_reject_unsupported_url_or_embedded_credentials(
    monkeypatch, url
):
    calls, resolve = setup_network(
        monkeypatch,
        lambda _: httpx.Response(200, content=DOCUMENT),
    )
    assert await url_resolver.PluginURLResolver()._try_fetch_yaml(url) is None
    assert calls == []
    resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme,port", [("https", 443), ("http", 80)])
async def test_public_request_pins_address_and_keeps_host_path_query(
    monkeypatch,
    scheme,
    port,
):
    calls, resolve = setup_network(
        monkeypatch,
        lambda _: httpx.Response(200, content=DOCUMENT),
    )
    result = await url_resolver.PluginURLResolver()._try_fetch_yaml(
        f"{scheme}://source.test/releases/plugin.yml?token={PRIVATE}",
    )
    assert result == {"id": "fixture", "name": "Fixture", "version": "1"}
    resolve.assert_awaited_once_with("source.test", port)
    request = calls[0]
    assert request.url.host == "93.184.216.34"
    assert request.url.path == "/releases/plugin.yml"
    assert request.url.params["token"] == PRIVATE
    assert request.headers["host"] == "source.test"
    assert request.headers["accept-encoding"] == "identity"
    assert request.extensions["sni_hostname"] == "source.test"


@pytest.mark.asyncio
async def test_redirect_rechecks_dns_and_does_not_connect_to_private_target(
    monkeypatch,
):
    def respond(request):
        if len(calls) == 1:
            return httpx.Response(
                302,
                headers={
                    "Location": "https://internal.test/private.yml",
                },
            )
        return httpx.Response(200, content=DOCUMENT)

    calls, resolve = setup_network(monkeypatch, respond)
    resolve.side_effect = [["93.184.216.34"], ["127.0.0.1"]]
    assert (
        await url_resolver.PluginURLResolver()._try_fetch_yaml(
            "https://source.test/plugin.yml",
        )
        is None
    )
    assert resolve.await_count == 2
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_public_redirect_preserves_configuration(monkeypatch):
    def respond(request):
        if len(calls) == 1:
            return httpx.Response(307, headers={"Location": "/latest.yml"})
        return httpx.Response(200, content=DOCUMENT)

    calls, resolve = setup_network(monkeypatch, respond)
    assert await url_resolver.PluginURLResolver()._try_fetch_yaml(
        "https://source.test/plugin.yml",
    ) == {"id": "fixture", "name": "Fixture", "version": "1"}
    assert resolve.await_count == 2
    assert [r.url.path for r in calls] == ["/plugin.yml", "/latest.yml"]
    assert all(r.url.host == "93.184.216.34" for r in calls)


@pytest.mark.asyncio
async def test_relative_assets_use_the_final_document_directory(monkeypatch):
    def respond(request):
        if len(calls) == 1:
            return httpx.Response(
                302,
                headers={
                    "Location": "https://cdn.test/releases/v2/plugin.yml",
                },
            )
        return httpx.Response(
            200,
            content=DOCUMENT
            + (b"source:\n  binary:\n    url: assets/plugin.zip\n"),
        )

    calls, _ = setup_network(monkeypatch, respond)
    result = await url_resolver.PluginURLResolver().resolve(
        "https://source.test/plugin.yml",
    )
    assert result["source"]["binary"]["url"] == (
        "https://cdn.test/releases/v2/assets/plugin.zip"
    )


@pytest.mark.asyncio
async def test_https_redirect_cannot_downgrade_to_plain_http(monkeypatch):
    def respond(request):
        return (
            httpx.Response(
                302,
                headers={
                    "Location": "http://source.test/plain.yml",
                },
            )
            if len(calls) == 1
            else httpx.Response(200, content=DOCUMENT)
        )

    calls, _ = setup_network(monkeypatch, respond)
    assert (
        await url_resolver.PluginURLResolver()._try_fetch_yaml(
            "https://source.test/plugin.yml",
        )
        is None
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_redirects_have_a_fixed_hop_limit(monkeypatch):
    calls, _ = setup_network(
        monkeypatch,
        lambda _: httpx.Response(
            302,
            headers={
                "Location": f"/hop-{len(calls)}.yml",
            },
        ),
    )
    assert (
        await url_resolver.PluginURLResolver()._try_fetch_yaml(
            "https://source.test/plugin.yml",
        )
        is None
    )
    assert len(calls) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Length": "1048577"},
        {"Content-Length": "-1"},
        {"Content-Length": "bad"},
        {"Content-Length": "9" * 5000},
        {"Content-Encoding": "gzip"},
    ],
)
async def test_reject_bad_size_or_encoding_before_consuming_body(
    monkeypatch,
    headers,
):
    class Body(httpx.AsyncByteStream):
        reads = 0
        closed = False

        async def __aiter__(self):
            self.reads += 1
            yield DOCUMENT

        async def aclose(self):
            self.closed = True

    body = Body()
    setup_network(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            headers=headers,
            stream=body,
        ),
    )
    assert (
        await url_resolver.PluginURLResolver()._try_fetch_yaml(
            "https://source.test/plugin.yml",
        )
        is None
    )
    assert body.reads == 0 and body.closed


@pytest.mark.asyncio
async def test_stream_limit_stops_reading_and_closes_upstream(monkeypatch):
    class Body(httpx.AsyncByteStream):
        reads = 0
        closed = False

        async def __aiter__(self):
            for _ in range(3):
                self.reads += 1
                yield b"x" * 600_000

        async def aclose(self):
            self.closed = True

    body = Body()
    setup_network(
        monkeypatch,
        lambda _: httpx.Response(200, stream=body),
    )
    assert (
        await url_resolver.PluginURLResolver()._try_fetch_yaml(
            "https://source.test/plugin.yml",
        )
        is None
    )
    assert body.reads == 2 and body.closed


@pytest.mark.asyncio
async def test_dns_and_stream_reads_share_the_deadline(monkeypatch):
    closed = []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.1)
            yield DOCUMENT

        async def aclose(self):
            closed.append(True)

    setup_network(
        monkeypatch,
        lambda _: httpx.Response(200, stream=SlowBody()),
    )
    monkeypatch.setattr(
        url_resolver,
        "SOURCE_FETCH_TIMEOUT",
        0.01,
        raising=False,
    )
    assert (
        await url_resolver.PluginURLResolver()._try_fetch_yaml(
            "https://source.test/plugin.yml",
        )
        is None
    )
    assert closed == [True]


@pytest.mark.asyncio
async def test_blocked_target_has_no_url_or_private_exception_in_logs(
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.DEBUG)
    calls, resolve = setup_network(
        monkeypatch,
        lambda _: httpx.Response(200, content=DOCUMENT),
    )
    resolve.side_effect = OSError(PRIVATE)
    assert (
        await url_resolver.PluginURLResolver()._try_fetch_yaml(
            f"https://source.test/plugin.yml?token={PRIVATE}",
        )
        is None
    )
    assert calls == []
    assert PRIVATE not in "\n".join(str(r.__dict__) for r in caplog.records)


@pytest.mark.asyncio
async def test_slow_dns_is_part_of_the_same_attempt_deadline(monkeypatch):
    async def delayed(host, port):
        await asyncio.sleep(0.1)
        return ["93.184.216.34"]

    calls, resolve = setup_network(
        monkeypatch,
        lambda _: httpx.Response(200, content=DOCUMENT),
    )
    resolve.side_effect = delayed
    monkeypatch.setattr(url_resolver, "SOURCE_FETCH_TIMEOUT", 0.01)
    assert (
        await url_resolver.PluginURLResolver()._try_fetch_yaml(
            "https://source.test/plugin.yml",
        )
        is None
    )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address,port",
    [
        ("93.184.216.34", 8443),
        ("2606:4700:4700::1111", 443),
    ],
)
async def test_public_custom_ports_and_ipv6_remain_usable(
    monkeypatch,
    address,
    port,
):
    calls, resolve = setup_network(
        monkeypatch,
        lambda _: httpx.Response(200, content=DOCUMENT),
        [address],
    )
    result = await url_resolver.PluginURLResolver()._try_fetch_yaml(
        f"https://source.test:{port}/plugin.yml",
    )
    assert result["id"] == "fixture"
    resolve.assert_awaited_once_with("source.test", port)
    assert calls[0].url.host == address
    assert (calls[0].url.port or 443) == port


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cycle", "missing", "status"])
async def test_invalid_redirect_and_status_bodies_are_not_consumed(
    monkeypatch,
    kind,
):
    class Body(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            raise AssertionError("non-success body must not be consumed")
            yield b""

        async def aclose(self):
            self.closed = True

    bodies = []

    def respond(request):
        body = Body()
        bodies.append(body)
        return httpx.Response(
            503 if kind == "status" else 302,
            headers={"Location": "/plugin.yml"} if kind == "cycle" else {},
            stream=body,
        )

    calls, _ = setup_network(monkeypatch, respond)
    assert (
        await url_resolver.PluginURLResolver()._try_fetch_yaml(
            "https://source.test/plugin.yml",
        )
        is None
    )
    assert len(calls) == 1
    assert all(body.closed for body in bodies)


@pytest.mark.asyncio
async def test_consumed_body_cannot_bypass_size_check_with_a_false_length():
    resolve = AsyncMock(return_value=["93.184.216.34"])
    transport = plugin_source_transport.PluginSourceTransport(
        resolver=resolve,
        max_response_bytes=8,
        inner=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=b"x" * 9,
                headers={"Content-Length": "1"},
            )
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(plugin_source_transport.PluginSourceRejected):
            await client.get("https://source.test/plugin.yml")


def test_invalid_response_limit_is_rejected_before_creating_transport():
    with pytest.raises(plugin_source_transport.PluginSourceRejected):
        plugin_source_transport.PluginSourceTransport(max_response_bytes=0)


@pytest.mark.asyncio
async def test_source_transport_only_allows_get():
    resolve = AsyncMock(return_value=["93.184.216.34"])
    calls = []
    transport = plugin_source_transport.PluginSourceTransport(
        resolver=resolve,
        inner=httpx.MockTransport(lambda request: calls.append(request)),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(plugin_source_transport.PluginSourceRejected):
            await client.post("https://source.test/plugin.yml")
    resolve.assert_not_awaited()
    assert calls == []


@pytest.mark.asyncio
async def test_cancelled_source_read_closes_body_and_preserves_cancellation(
    monkeypatch,
):
    started = asyncio.Event()
    closed = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Future()
            yield DOCUMENT

        async def aclose(self):
            closed.append(True)

    setup_network(monkeypatch, lambda _: httpx.Response(200, stream=Body()))
    pending = asyncio.create_task(
        url_resolver.PluginURLResolver()._try_fetch_yaml(
            "https://source.test/plugin.yml",
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert closed == [True]
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_github_main_master_fallback_and_relative_asset_still_work(
    monkeypatch,
):
    def respond(request):
        if "/master/" not in request.url.path:
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=DOCUMENT
            + (b"source:\n  binary:\n    url: assets/plugin.zip\n"),
        )

    calls, _ = setup_network(monkeypatch, respond)
    result = await url_resolver.PluginURLResolver().resolve(
        "https://github.com/owner/fixture",
    )
    assert [r.url.path for r in calls] == [
        "/owner/fixture/main/plugin.yml",
        "/owner/fixture/main/plugin.yaml",
        "/owner/fixture/master/plugin.yml",
    ]
    assert result["source"]["binary"]["url"] == (
        "https://raw.githubusercontent.com/owner/fixture/"
        "master/assets/plugin.zip"
    )


@pytest.mark.asyncio
async def test_api_rejects_internal_source_without_connecting(monkeypatch):
    calls, _ = setup_network(
        monkeypatch,
        lambda _: httpx.Response(200, content=DOCUMENT),
        ["169.254.169.254"],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime_app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/plugins/fetch-info",
            json={
                "url": f"http://metadata.test/plugin.yml?token={PRIVATE}",
            },
        )
    assert response.status_code == 400
    assert PRIVATE not in response.text and calls == []
