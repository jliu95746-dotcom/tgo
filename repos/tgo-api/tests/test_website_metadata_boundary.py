"""Website previews reject private targets and never echo failure payloads."""

import asyncio
from ipaddress import ip_address
import logging
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
from httpx import ASGITransport, AsyncClient
import pytest

from app.api.v1.endpoints import utils as gateway

PRIVATE = "synthetic-private-website-query"
BASE = "https://site.example.invalid/catalog/"
USER = SimpleNamespace(id=uuid4())


@pytest.fixture
def website(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    original = httpx.AsyncClient
    state = SimpleNamespace(
        requests=[],
        options=[],
        resolutions=[],
        error=None,
        delay=0,
        responses=[httpx.Response(200, text="<title>商品资料</title>")],
        answers={
            "site.example.invalid": ["93.184.216.34"],
            "other.example.invalid": ["1.1.1.1"],
        },
    )

    async def resolve(host, port):
        state.resolutions.append((host, port))
        if state.delay:
            await asyncio.sleep(state.delay)
        sequence = getattr(state, "dns_sequence", None)
        if sequence is not None:
            return next(sequence)
        try:
            return [str(ip_address(host))]
        except ValueError:
            result = state.answers.get(host, [])
            if isinstance(result, Exception):
                raise result
            return result

    async def handle(request):
        state.requests.append(request)
        if state.error is not None:
            raise state.error
        return state.responses.pop(0)

    def factory(**kwargs):
        state.options.append(kwargs.copy())
        transport = kwargs.get("transport")
        if transport is not None:
            transport.resolver = resolve
            transport.inner = httpx.MockTransport(handle)
        else:
            kwargs["transport"] = httpx.MockTransport(handle)
        return original(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return state


async def preview(url=BASE):
    return await gateway.extract_website_metadata(
        gateway.WebsiteMetadataRequest(url=url), USER
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "http://[::ffff:127.0.0.1]/",
        "https://user:synthetic-password@site.example.invalid/",
    ],
)
async def test_private_targets_and_url_credentials_never_reach_http(
    website, url
):
    with pytest.raises(HTTPException) as failure:
        await preview(url)
    assert failure.value.status_code == 400
    assert not website.requests


@pytest.mark.asyncio
async def test_same_host_redirect_revalidates_dns_and_stops_rebinding(website):
    website.dns_sequence = iter([["93.184.216.34"], ["127.0.0.1"]])
    website.responses = [
        httpx.Response(302, headers={"location": "/changed"}),
        httpx.Response(200),
    ]
    with pytest.raises(HTTPException) as failure:
        await preview()
    assert failure.value.status_code == 400
    assert len(website.requests) == 1
    assert len(website.resolutions) == 2


@pytest.mark.asyncio
async def test_dns_failure_has_static_gateway_error(website, caplog):
    website.answers["site.example.invalid"] = OSError(PRIVATE)
    with pytest.raises(HTTPException) as failure:
        await preview(BASE + "?key=" + PRIVATE)
    assert failure.value.status_code == 502
    assert not website.requests
    assert PRIVATE not in str(failure.value.detail) + caplog.text


@pytest.mark.asyncio
async def test_dns_timeout_is_bounded_by_overall_deadline(
    website, monkeypatch
):
    website.delay = 1
    monkeypatch.setattr(gateway, "REQUEST_TIMEOUT", 0.01)
    with pytest.raises(HTTPException) as failure:
        await preview()
    assert failure.value.status_code == 504
    assert not website.requests


@pytest.mark.asyncio
async def test_http_log_filter_is_reset_after_failure(website, caplog):
    caplog.set_level(logging.INFO)
    website.error = httpx.ConnectError(PRIVATE)
    with pytest.raises(HTTPException):
        await preview(BASE + "?key=" + PRIVATE)
    logging.getLogger("httpx").info("unrelated-request-still-visible")
    assert "unrelated-request-still-visible" in caplog.text
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "file:///private",
        "https://user:password@site.example.invalid/",
        "http://10.0.0.1/",
        "http://[bad",
    ],
)
async def test_unsafe_redirect_target_does_not_receive_request(
    website, location
):
    website.responses = [
        httpx.Response(302, headers={"location": location}),
        httpx.Response(200),
    ]
    with pytest.raises(HTTPException) as failure:
        await preview()
    assert failure.value.status_code == 400
    assert len(website.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cycle", "too_many", "missing_location"])
async def test_redirect_sequences_are_bounded(website, mode):
    if mode == "cycle":
        website.responses = [httpx.Response(302, headers={"location": BASE})]
    elif mode == "missing_location":
        website.responses = [httpx.Response(302)]
    else:
        website.responses = [
            httpx.Response(302, headers={"location": f"/step/{i}"})
            for i in range(6)
        ]
    with pytest.raises(HTTPException) as failure:
        await preview()
    assert failure.value.status_code == 502
    assert len(website.requests) == (6 if mode == "too_many" else 1)


@pytest.mark.asyncio
async def test_default_size_limit_rejects_large_body(website):
    website.responses = [httpx.Response(200, content=b"x" * 1_048_577)]
    with pytest.raises(HTTPException) as failure:
        await preview()
    assert failure.value.status_code == 502


@pytest.mark.asyncio
async def test_body_read_timeout_closes_the_received_stream(
    website, monkeypatch
):
    class SlowPage(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            await asyncio.sleep(1)
            yield b"late page"

        async def aclose(self):
            self.closed = True

    page = SlowPage()
    website.responses = [httpx.Response(200, stream=page)]
    monkeypatch.setattr(gateway, "REQUEST_TIMEOUT", 0.01)
    with pytest.raises(HTTPException) as failure:
        await preview()
    assert failure.value.status_code == 504
    assert page.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,address,port",
    [
        ("http://site.example.invalid/catalog", "93.184.216.34", 80),
        ("https://site.example.invalid:8443/catalog", "1.1.1.1", 8443),
        ("https://site.example.invalid/catalog", "2606:4700:4700::1111", 443),
    ],
)
async def test_public_http_custom_port_and_ipv6_remain_supported(
    website, url, address, port
):
    website.answers["site.example.invalid"] = [address]
    result = await preview(url)
    assert result.success and result.title == "商品资料"
    assert website.requests[0].url.host == address
    assert website.resolutions == [("site.example.invalid", port)]


@pytest.mark.asyncio
async def test_declared_html_encoding_is_preserved(website):
    website.responses = [
        httpx.Response(
            200,
            content="<title>中文资料</title>".encode("gb18030"),
            headers={"content-type": "text/html; charset=gb18030"},
        )
    ]
    assert (await preview()).title == "中文资料"


@pytest.mark.asyncio
async def test_very_long_size_header_is_rejected_as_gateway_error(website):
    website.responses = [
        httpx.Response(
            200, headers={"content-length": "9" * 5000}, text="short"
        )
    ]
    with pytest.raises(HTTPException) as failure:
        await preview()
    assert failure.value.status_code == 502


def test_description_fallback_and_malformed_link():
    result = gateway._extract_metadata(
        '<title>A<b>B</b></title><meta name="description" content=" ">'
        '<meta property="og:description" content="备用介绍">'
        '<link rel="icon" href="http://[bad">',
        BASE,
    )
    assert result["title"] == "AB"
    assert result["description"] == "备用介绍"
    assert result["favicon"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [[], ["93.184.216.34", "127.0.0.1"], ["not-an-ip"], ["1.1.1.1"] * 33],
)
async def test_all_dns_answers_must_be_verifiable_public_addresses(
    website, answers
):
    website.answers["site.example.invalid"] = answers
    with pytest.raises(HTTPException) as failure:
        await preview()
    assert failure.value.status_code == 400
    assert not website.requests


@pytest.mark.asyncio
async def test_connection_is_pinned_and_retains_host_sni_path_query(
    website, caplog
):
    caplog.set_level(logging.DEBUG)
    result = await preview(BASE + "item?q=" + PRIVATE)
    request = website.requests[0]
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "site.example.invalid"
    assert request.extensions["sni_hostname"] == "site.example.invalid"
    assert request.url.raw_path == ("/catalog/item?q=" + PRIVATE).encode()
    assert request.method == "GET" and request.content == b""
    assert request.headers["accept-encoding"] == "identity"
    assert result.url == BASE + "item?q=" + PRIVATE
    assert result.title == "商品资料" and result.success
    assert PRIVATE not in caplog.text
    assert website.options[0]["follow_redirects"] is False
    assert website.options[0]["trust_env"] is False


@pytest.mark.asyncio
async def test_public_redirects_remain_supported_and_use_final_base(website):
    website.responses = [
        httpx.Response(
            302,
            headers={"location": "https://other.example.invalid/store/page"},
        ),
        httpx.Response(
            200,
            text=(
                '<title>资料</title><link rel="icon" href="icon.png">'
                '<meta property="og:image" content="../cover.jpg">'
            ),
        ),
    ]
    result = await preview()
    assert result.url == BASE
    assert result.favicon == "https://other.example.invalid/store/icon.png"
    assert result.og_image == "https://other.example.invalid/cover.jpg"
    assert [request.url.host for request in website.requests] == [
        "93.184.216.34",
        "1.1.1.1",
    ]


@pytest.mark.asyncio
async def test_redirect_to_private_target_stops_before_second_request(website):
    website.responses = [
        httpx.Response(302, headers={"location": "http://127.0.0.1/private"}),
        httpx.Response(200),
    ]
    with pytest.raises(HTTPException) as failure:
        await preview()
    assert failure.value.status_code == 400
    assert len(website.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code", [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)]
)
async def test_transport_failure_has_static_details(
    website, error, code, caplog
):
    caplog.set_level(logging.DEBUG)
    website.error = error(PRIVATE)
    with pytest.raises(HTTPException) as failure:
        await preview(BASE + "?key=" + PRIVATE)
    assert failure.value.status_code == code
    assert PRIVATE not in str(failure.value.detail) + caplog.text


@pytest.mark.asyncio
async def test_unexpected_failure_keeps_existing_unsuccessful_response_shape(
    website, caplog
):
    website.error = RuntimeError(PRIVATE)
    result = await preview(BASE + "?key=" + PRIVATE)
    assert result.success is False
    assert result.error
    assert PRIVATE not in result.error + caplog.text
    assert (
        result.title
        is result.description
        is result.favicon
        is result.og_image
        is None
    )


@pytest.mark.asyncio
async def test_http_error_keeps_status_and_hides_upstream_body(
    website, caplog
):
    website.responses = [httpx.Response(403, text=PRIVATE)]
    with pytest.raises(HTTPException) as failure:
        await preview(BASE + "?key=" + PRIVATE)
    assert failure.value.status_code == 502
    assert "403" in failure.value.detail
    assert PRIVATE not in str(failure.value.detail) + caplog.text


@pytest.mark.parametrize(
    "link",
    ["/favicon.ico", "icons/favicon.ico", "//cdn.example.invalid/favicon.ico"],
)
def test_metadata_image_links_are_resolved_as_http_urls(link):
    expected = {
        "/favicon.ico": "https://site.example.invalid/favicon.ico",
        "icons/favicon.ico": BASE + "icons/favicon.ico",
        "//cdn.example.invalid/favicon.ico": (
            "https://cdn.example.invalid/favicon.ico"
        ),
    }[link]
    result = gateway._extract_metadata(
        f'<link rel="shortcut icon" href="{link}">'
        f'<meta property="og:image" content="{link}">',
        BASE,
    )
    assert result["favicon"] == result["og_image"] == expected


@pytest.mark.parametrize(
    "link", ["javascript:alert(1)", "data:text/html,test", "file:///private"]
)
def test_non_web_metadata_links_are_not_returned(link):
    result = gateway._extract_metadata(
        f'<link rel="icon" href="{link}">'
        f'<meta property="og:image" content="{link}">',
        BASE,
    )
    assert result["favicon"] is result["og_image"] is None


def test_plain_and_empty_metadata_remain_valid():
    assert gateway._extract_metadata("<html></html>", BASE) == {
        "title": None,
        "description": None,
        "favicon": None,
        "og_image": None,
    }
    result = gateway._extract_metadata(
        '<title> 商品资料 </title><meta name="description" content=" 真实介绍 ">', BASE
    )
    assert result["title"] == "商品资料" and result["description"] == "真实介绍"


@pytest.mark.asyncio
async def test_staff_authentication_runs_before_any_fetch(website):
    application = FastAPI()
    application.include_router(gateway.router)

    def denied():
        raise HTTPException(401, "Authentication required")

    application.dependency_overrides[gateway.get_current_active_user] = denied
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/extract-website-metadata", json={"url": BASE}
        )
    assert response.status_code == 401
    assert not website.requests
