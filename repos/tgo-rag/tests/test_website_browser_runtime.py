"""Opt-in real Chromium checks against an isolated loopback website.

Run with RUN_BROWSER_CRAWL_TESTS=1 in the RAG worker runtime. No database,
provider credentials, production sites, or customer records are accessed.
"""

import asyncio
import gc
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from src.rag_service.services import crawler as service


@unittest.skipUnless(
    os.environ.get("RUN_BROWSER_CRAWL_TESTS") == "1",
    "Set RUN_BROWSER_CRAWL_TESTS=1 in a runtime with Chromium installed",
)
class TestWebsiteBrowserRuntime(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop_errors = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        def record_exception(event_loop, context):
            self.loop_errors.append(context.get("message", "async error"))
            if previous_handler is not None:
                previous_handler(event_loop, context)
            else:
                event_loop.default_exception_handler(context)

        loop.set_exception_handler(record_exception)
        self.addCleanup(loop.set_exception_handler, previous_handler)
        self.cache = tempfile.TemporaryDirectory(prefix="tgo-browser-crawl-")
        self.addCleanup(self.cache.cleanup)
        self.environment = patch.dict(
            os.environ, {"CRAWL4_AI_BASE_DIRECTORY": self.cache.name}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.crawlers = []
        original_crawler = service.AsyncWebCrawler

        def tracked_crawler(*args, **kwargs):
            crawler = original_crawler(
                *args, base_directory=self.cache.name, **kwargs
            )
            self.crawlers.append(crawler)
            return crawler

        self.crawler_patch = patch.object(
            service, "AsyncWebCrawler", tracked_crawler
        )
        self.crawler_patch.start()
        self.addCleanup(self.crawler_patch.stop)
        self.version = "first"
        self.requests = []
        self.handlers = set()
        self.slow_started = asyncio.Event()
        self.server = await asyncio.start_server(self.respond, "127.0.0.1", 0)
        self.base = (
            f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        )

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        handlers = list(self.handlers)
        for handler in handlers:
            handler.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
        for crawler in self.crawlers:
            manager = getattr(
                crawler.crawler_strategy, "browser_manager", None
            )
            if manager is not None:
                self.assertIsNone(
                    manager.playwright, "Owned driver was not released"
                )
        self.crawlers.clear()
        gc.collect()
        await asyncio.sleep(0)
        self.assertEqual(
            self.loop_errors, [], "Unobserved asynchronous failure"
        )

    async def respond(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            raw = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            path = raw.split(" ", 2)[1]
            self.requests.append((path, raw))
            status = "200 OK"
            if path == "/robots.txt":
                body = "User-agent: *\nDisallow: /private\n"
            elif path == "/missing":
                status, body = "404 Not Found", "Not found"
            elif path == "/slow":
                self.slow_started.set()
                await asyncio.sleep(30)
                body = "Too late"
            else:
                body = (
                    "<html><head><title>Owned dynamic fixture</title></head>"
                    '<body><p id="details">Static placeholder</p>'
                    "<script>setTimeout(() => {"
                    "document.getElementById('details').textContent = "
                    f"'Rendered catalog version {self.version}';"
                    "const link = document.createElement('a');"
                    "link.href='/next'; link.textContent='Next product';"
                    "document.body.appendChild(link);"
                    "}, 150);</script></body></html>"
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
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.handlers.discard(task)

    def crawler(self, *, render_js=True, timeout=30):
        # Functional checks use the production default, including cold startup.
        # The dedicated deadline test explicitly uses a 3-second budget.
        return service.WebCrawlerService(
            service.CrawlOptions(
                render_js=render_js,
                delay_seconds=0,
                wait_time=0.5,
                timeout_seconds=timeout,
                headers={"X-Fixture": "owned-browser"},
                user_agent="OwnedBrowserCrawler/1.0",
            )
        )

    async def test_script_execution_and_fresh_recrawl(self):
        static = await self.crawler(render_js=False).crawl_page(
            self.base + "/help"
        )
        self.assertIn("Static placeholder", static.content_markdown)
        self.assertNotIn("Rendered catalog", static.content_markdown)
        crawler = self.crawler()
        first = await crawler.crawl_page(self.base + "/help")
        self.assertIn("Rendered catalog version first", first.content_markdown)
        self.assertNotIn("Static placeholder", first.content_markdown)
        self.assertEqual(first.title, "Owned dynamic fixture")
        self.assertEqual(first.links, [self.base + "/next"])
        self.version = "second"
        second = await crawler.crawl_page(self.base + "/help")
        self.assertIn(
            "Rendered catalog version second", second.content_markdown
        )
        self.assertNotEqual(first.content_hash, second.content_hash)
        requests = [
            raw.lower() for path, raw in self.requests if path == "/help"
        ]
        self.assertEqual(len(requests), 3)
        for raw in requests:
            self.assertIn("x-fixture: owned-browser", raw)
            self.assertIn("user-agent: ownedbrowsercrawler/1.0", raw)

    async def test_http_failure_and_robots_denial_then_recovery(self):
        crawler = self.crawler()
        with self.assertRaises(service.CrawlError) as denied:
            await crawler.crawl_page(self.base + "/private")
        self.assertEqual(denied.exception.code, "robots_denied")
        self.assertFalse(any(path == "/private" for path, _ in self.requests))
        with self.assertRaises(service.CrawlError) as missing:
            await crawler.crawl_page(self.base + "/missing")
        self.assertEqual(missing.exception.code, "http_error")
        self.assertEqual(missing.exception.http_status_code, 404)
        recovered = await crawler.crawl_page(self.base + "/help")
        self.assertIn(
            "Rendered catalog version first", recovered.content_markdown
        )

    async def test_timeout_is_bounded_and_next_crawl_recovers(self):
        started = time.monotonic()
        with self.assertRaises(service.CrawlError) as failure:
            await self.crawler(timeout=3).crawl_page(self.base + "/slow")
        self.assertEqual(failure.exception.code, "timeout")
        # Includes bounded context close (5s) and driver-stop fallback (3s).
        self.assertLess(time.monotonic() - started, 14)
        # The deadline includes browser/driver startup, not just navigation.
        # A cold or resource-constrained host can time out before /slow is sent.
        # The cancellation test separately verifies a request reaches /slow.
        recovered = await self.crawler().crawl_page(self.base + "/help")
        self.assertIn(
            "Rendered catalog version first", recovered.content_markdown
        )

    async def test_request_cancellation_releases_driver_and_recovers(self):
        request = asyncio.create_task(
            self.crawler().crawl_page(self.base + "/slow")
        )
        try:
            await asyncio.wait_for(self.slow_started.wait(), timeout=15)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
        finally:
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        recovered = await self.crawler().crawl_page(self.base + "/help")
        self.assertIn(
            "Rendered catalog version first", recovered.content_markdown
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
