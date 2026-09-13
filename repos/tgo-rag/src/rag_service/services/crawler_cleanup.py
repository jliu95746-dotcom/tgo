"""Bounded cleanup of per-crawl resources, including partial shutdown."""

import asyncio

from crawl4ai import AsyncWebCrawler
from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy

from ..logging_config import get_logger

logger = get_logger(__name__)


async def close_crawler(crawler: AsyncWebCrawler) -> None:
    """Stop an owned driver if crawl4ai cannot finish closing its contexts."""
    try:
        async with asyncio.timeout(5):
            await crawler.close()
        return
    except Exception as error:
        logger.warning(
            "Crawler cleanup failed", error_type=type(error).__name__
        )

    strategy = getattr(crawler, "crawler_strategy", None)
    if not isinstance(strategy, AsyncPlaywrightCrawlerStrategy):
        return
    manager = strategy.browser_manager
    # This fallback is only for a driver owned by this crawl, never a remote
    # browser supplied by another application through CDP.
    if manager.config.cdp_url or manager.playwright is None:
        return
    try:
        async with asyncio.timeout(3):
            await manager.playwright.stop()
        manager.playwright = None
    except Exception as error:
        logger.warning(
            "Crawler driver cleanup failed", error_type=type(error).__name__
        )
