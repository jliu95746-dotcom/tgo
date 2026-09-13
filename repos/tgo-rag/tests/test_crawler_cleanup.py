"""Crawler cleanup must release its driver even if context shutdown fails."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy

from src.rag_service.services.crawler_cleanup import close_crawler


class TestCrawlerCleanup(unittest.IsolatedAsyncioTestCase):
    def crawler(self, failure=None):
        driver = SimpleNamespace(stop=AsyncMock())
        strategy = Mock(spec=AsyncPlaywrightCrawlerStrategy)
        strategy.browser_manager = SimpleNamespace(
            playwright=driver, config=SimpleNamespace(cdp_url=None)
        )
        return (
            SimpleNamespace(
                close=AsyncMock(side_effect=failure), crawler_strategy=strategy
            ),
            driver,
        )

    async def test_success_does_not_stop_driver_twice(self):
        crawler, driver = self.crawler()
        await close_crawler(crawler)
        crawler.close.assert_awaited_once()
        driver.stop.assert_not_awaited()

    async def test_context_close_timeout_still_stops_owned_driver(self):
        crawler, driver = self.crawler(TimeoutError())
        await close_crawler(crawler)
        driver.stop.assert_awaited_once()
        self.assertIsNone(crawler.crawler_strategy.browser_manager.playwright)

    async def test_context_close_error_still_stops_owned_driver(self):
        crawler, driver = self.crawler(RuntimeError("fixture failure"))
        await close_crawler(crawler)
        driver.stop.assert_awaited_once()

    async def test_http_strategy_has_no_browser_fallback(self):
        crawler = SimpleNamespace(
            close=AsyncMock(side_effect=TimeoutError()),
            crawler_strategy=SimpleNamespace(),
        )
        await close_crawler(crawler)

    async def test_external_browser_is_not_stopped(self):
        crawler, driver = self.crawler(TimeoutError())
        crawler.crawler_strategy.browser_manager.config.cdp_url = (
            "owned-fixture"
        )
        await close_crawler(crawler)
        driver.stop.assert_not_awaited()

    async def test_fallback_failure_is_safe_and_does_not_mask_crawl_result(
        self,
    ):
        crawler, driver = self.crawler(TimeoutError())
        driver.stop.side_effect = RuntimeError("fixture-private-value")
        await close_crawler(crawler)


if __name__ == "__main__":
    unittest.main()
