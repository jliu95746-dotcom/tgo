"""Static HTML fetching honors options without requiring a browser."""

import asyncio
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.rag_service.services import crawler as service
from src.rag_service.tasks import website_crawling as tasks


def successful_result(**overrides):
    values = {
        "success": True,
        "url": "https://example.test/help",
        "markdown": "可以选择蓝色或黑色。",
        "metadata": {"title": "商品说明"},
        "status_code": 200,
        "links": {},
        "error_message": "",
    }
    return SimpleNamespace(**{**values, **overrides})


def stub_crawler(monkeypatch, result):
    captured = {}

    class StubCrawler:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def start(self):
            return self

        async def close(self):
            pass

        async def arun(self, **kwargs):
            captured["run_config"] = kwargs["config"]
            return result

    monkeypatch.setattr(service, "AsyncWebCrawler", StubCrawler)
    return captured


@pytest.mark.asyncio
async def test_static_fetch_uses_http_strategy_and_preserves_options(
    monkeypatch,
):
    captured = stub_crawler(monkeypatch, successful_result())
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    crawler = service.WebCrawlerService(
        service.CrawlOptions(
            render_js=False,
            respect_robots_txt=True,
            timeout_seconds=7,
            delay_seconds=0.2,
            user_agent="OwnedCrawlerTest/1.0",
            headers={"X-Fixture": "owned"},
        )
    )
    page = await crawler.crawl_page("https://example.test/help")
    strategy = captured["crawler_strategy"]
    assert isinstance(strategy, service.AsyncHTTPCrawlerStrategy)
    assert strategy.browser_config.headers["X-Fixture"] == "owned"
    assert (
        strategy.browser_config.headers["User-Agent"] == "OwnedCrawlerTest/1.0"
    )
    assert strategy.browser_config.verify_ssl is True
    assert captured["config"].user_agent == "OwnedCrawlerTest/1.0"
    assert captured["run_config"].check_robots_txt is True
    assert captured["run_config"].page_timeout == 7
    sleep.assert_awaited_once_with(0.2)
    assert page.content_markdown == "可以选择蓝色或黑色。"


@pytest.mark.asyncio
async def test_dynamic_fetch_keeps_browser_with_millisecond_timeout(
    monkeypatch,
):
    captured = stub_crawler(monkeypatch, successful_result())
    await service.WebCrawlerService(
        service.CrawlOptions(
            render_js=True,
            delay_seconds=0,
            timeout_seconds=7,
            respect_robots_txt=False,
            headers={"X-Fixture": "dynamic"},
            wait_time=1.25,
        )
    ).crawl_page("https://example.test/help")
    assert captured.get("crawler_strategy") is None
    assert captured["config"].java_script_enabled is True
    assert captured["config"].ignore_https_errors is False
    assert captured["config"].headers["X-Fixture"] == "dynamic"
    assert captured["run_config"].page_timeout == 7000
    assert captured["run_config"].delay_before_return_html == 1.25
    assert captured["run_config"].check_robots_txt is False


def test_markdown_wrapper_and_stable_links_are_normalized():
    result = successful_result(
        markdown=SimpleNamespace(raw_markdown="# 商品说明\n蓝色现货"),
        links={
            "internal": [
                {"href": "/a#details"},
                {"href": "/b"},
                {"href": "/a"},
                {"href": "https://outside.test/a"},
                {"href": "mailto:no@example.test"},
            ],
            "external": [{"href": "https://example.test/b#top"}],
        },
    )
    page = service.WebCrawlerService()._build_crawled_page(
        result, result.url, 2
    )
    assert page.content_markdown == "# 商品说明\n蓝色现货"
    assert page.links == ["https://example.test/a", "https://example.test/b"]
    assert page.depth == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,code",
    [
        (
            {
                "success": False,
                "status_code": 403,
                "error_message": "Access denied by robots.txt",
            },
            "robots_denied",
        ),
        ({"success": True, "status_code": 404}, "http_error"),
        (
            {
                "success": False,
                "status_code": None,
                "error_message": (
                    "HTTP 503: token=secret\nCode context:\nTimeoutError"
                ),
            },
            "http_error",
        ),
        (
            {
                "success": False,
                "error_message": "Request timed out token=secret",
            },
            "timeout",
        ),
        (
            {
                "success": False,
                "error_message": "Executable doesn't exist at C:/private",
            },
            "browser_unavailable",
        ),
        ({"markdown": "   "}, "empty_content"),
    ],
)
async def test_failures_have_safe_specific_causes(
    monkeypatch, overrides, code
):
    stub_crawler(monkeypatch, successful_result(**overrides))
    with pytest.raises(service.CrawlError) as failure:
        await service.WebCrawlerService(
            service.CrawlOptions(delay_seconds=0)
        ).crawl_page("https://example.test/help?token=secret")
    assert failure.value.code == code
    assert "secret" not in str(failure.value)
    assert "C:/private" not in str(failure.value)


def test_task_config_does_not_drop_robots_or_headers():
    config = tasks.CrawlConfig.from_dict(
        {
            "respect_robots_txt": False,
            "headers": {"X-Fixture": "owned"},
        }
    )
    assert config.respect_robots_txt is False
    assert config.headers == {"X-Fixture": "owned"}
    assert tasks.CrawlConfig.from_dict({}).respect_robots_txt is True


def test_task_accepts_existing_collection_ui_field_names():
    config = tasks.CrawlConfig.from_dict({
        "js_rendering": True, "delay_between_requests": 2.5, "timeout": 19,
    })
    assert config.render_js is True
    assert config.delay_seconds == 2.5 and config.timeout_seconds == 19
    override = tasks.CrawlConfig.from_dict({
        "js_rendering": True, "render_js": False,
        "timeout": 19, "timeout_seconds": 8,
    })
    assert override.render_js is False and override.timeout_seconds == 8


@pytest.mark.asyncio
async def test_task_persists_safe_cause_and_forwards_options(monkeypatch):
    page_id = uuid4()
    config = tasks.CrawlConfig.from_dict(
        {
            "respect_robots_txt": False,
            "headers": {"X-Fixture": "owned"},
        }
    )
    info = tasks.PageInfo(
        page_id, "https://example.test/a", 0, uuid4(), uuid4(), {}
    )
    monkeypatch.setattr(
        tasks,
        "_load_page_and_config",
        AsyncMock(
            return_value=(info, config, None),
        ),
    )
    update = AsyncMock()
    monkeypatch.setattr(tasks, "_update_page_status", update)
    captured = {}

    class FailingCrawler:
        def __init__(self, options, **kwargs):
            captured["options"] = options

        async def crawl_page(self, *args, **kwargs):
            raise service.CrawlError("robots_denied", 403)

    monkeypatch.setattr(tasks, "WebCrawlerService", FailingCrawler)
    result = await tasks.crawl_page_async(page_id)
    assert (
        result["status"] == "failed"
        and result["error_code"] == "robots_denied"
    )
    assert captured["options"].respect_robots_txt is False
    assert captured["options"].headers == {"X-Fixture": "owned"}
    update.assert_awaited_once_with(
        page_id,
        "failed",
        result["error"],
        http_status_code=403,
    )


@pytest.mark.asyncio
async def test_total_timeout_also_bounds_crawler_startup(monkeypatch):
    class HangingCrawler:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            await asyncio.Event().wait()

        async def close(self):
            pass

    monkeypatch.setattr(service, "AsyncWebCrawler", HangingCrawler)
    with pytest.raises(service.CrawlError) as failure:
        await service.WebCrawlerService(
            service.CrawlOptions(
                delay_seconds=0,
                timeout_seconds=0.02,
            )
        ).crawl_page("https://example.test/a")
    assert failure.value.code == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_request", [False, True])
async def test_close_browser_before_draining_inflight_call(
    monkeypatch, cancel_request
):
    started, closed = asyncio.Event(), asyncio.Event()
    interrupted = []
    finished = []

    class PendingCrawler:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            pass

        async def arun(self, **kwargs):
            started.set()
            try:
                await closed.wait()
            except asyncio.CancelledError:
                interrupted.append(True)
                raise
            finally:
                finished.append(True)
            raise RuntimeError("Owned browser closed during navigation")

        async def close(self):
            closed.set()

    monkeypatch.setattr(service, "AsyncWebCrawler", PendingCrawler)
    request = asyncio.create_task(service.WebCrawlerService(service.CrawlOptions(
        delay_seconds=0, timeout_seconds=0.05,
    )).crawl_page("https://example.test/slow"))
    await started.wait()
    if cancel_request:
        request.cancel()
    expected = asyncio.CancelledError if cancel_request else service.CrawlError
    with pytest.raises(expected):
        await request
    assert closed.is_set() and finished == [True]
    assert interrupted == []


@pytest.mark.asyncio
async def test_failed_browser_startup_closes_partial_resources(monkeypatch):
    close = AsyncMock()

    class FailedStartup:
        def __init__(self, **kwargs):
            self.close = close

        async def start(self):
            raise RuntimeError("Executable doesn't exist at owned-fixture")

        __aenter__ = start

        async def __aexit__(self, *args):
            await close()

    monkeypatch.setattr(service, "AsyncWebCrawler", FailedStartup)
    with pytest.raises(service.CrawlError) as failure:
        await service.WebCrawlerService(service.CrawlOptions(
            render_js=True, delay_seconds=0,
        )).crawl_page("https://example.test/a")
    assert failure.value.code == "browser_unavailable"
    close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url", ["file:///secret", "https://user:password@example.test/a"]
)
async def test_rejects_non_web_or_credential_urls_before_fetch(
    monkeypatch, url
):
    captured = stub_crawler(monkeypatch, successful_result())
    with pytest.raises(service.CrawlError) as failure:
        await service.WebCrawlerService().crawl_page(url)
    assert failure.value.code == "invalid_url"
    assert not captured


@pytest.mark.asyncio
async def test_real_static_http_fetch_robots_and_404_without_chromium(
    monkeypatch,
    tmp_path,
):
    # Use a private test cache instead of the dependency's default home folder.
    monkeypatch.setenv("CRAWL4_AI_BASE_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(
        service,
        "AsyncWebCrawler",
        partial(
            service.AsyncWebCrawler,
            base_directory=str(tmp_path),
        ),
    )
    requests = []

    async def respond(reader, writer):
        try:
            raw = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            path = raw.split(" ", 2)[1]
            requests.append((path, raw))
            status = "200 OK"
            if path == "/robots.txt":
                body = "User-agent: *\nDisallow: /private\n"
            elif path == "/missing":
                body, status = "Not found", "404 Not Found"
            else:
                body = (
                    "<html><head><title>颜色说明</title></head><body>"
                    "<p>这款包有蓝色和黑色，肩带可以调节。</p>"
                    '<a href="/next">下一页</a>'
                    '<a href="https://outside.test/never-fetch">外站链接</a>'
                    "</body></html>"
                )
            payload = body.encode("utf-8")
            writer.write(
                (
                    f"HTTP/1.1 {status}\r\n"
                    "Content-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(payload)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    try:
        crawler = service.WebCrawlerService(
            service.CrawlOptions(
                delay_seconds=0,
                headers={"X-Fixture": "owned"},
                user_agent="OwnedCrawlerTest/1.0",
            )
        )
        page = await crawler.crawl_page(base + "/help")
        assert "蓝色和黑色" in page.content_markdown
        assert page.title == "颜色说明"
        assert page.links == [base + "/next"]
        fetched_request = next(
            raw for path, raw in requests if path == "/help"
        )
        assert "X-Fixture: owned" in fetched_request
        assert "User-Agent: OwnedCrawlerTest/1.0" in fetched_request
        with pytest.raises(service.CrawlError) as blocked:
            await crawler.crawl_page(base + "/private")
        assert blocked.value.code == "robots_denied"
        assert not any(path == "/private" for path, _ in requests)
        with pytest.raises(service.CrawlError) as missing:
            await crawler.crawl_page(base + "/missing")
        assert missing.value.code == "http_error"
        assert missing.value.http_status_code == 404
    finally:
        server.close()
        await server.wait_closed()
