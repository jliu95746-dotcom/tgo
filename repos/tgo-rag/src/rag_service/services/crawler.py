"""
Web crawler service using crawl4ai.

Single-page RAG crawling with crawl4ai content extraction.

Design: Single-page crawling with link extraction
- Each page is crawled independently
- Links are extracted for discovery of child pages
- Page hierarchy is managed externally (by website_crawling tasks)
"""

import asyncio
import fnmatch
import hashlib
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
)
from crawl4ai.async_configs import HTTPCrawlerConfig
from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
from crawl4ai.models import CrawlResult

from ..logging_config import get_logger
from .crawl_errors import CrawlError, classify_crawl_error
from .crawler_cleanup import close_crawler

logger = get_logger(__name__)


@dataclass
class CrawlOptions:
    """Configuration options for web crawling."""

    render_js: bool = False
    respect_robots_txt: bool = True
    delay_seconds: float = 1.0
    user_agent: Optional[str] = None
    timeout_seconds: int = 30
    headers: Optional[Dict[str, str]] = None
    wait_time: float = 0
    follow_external_links: bool = False


@dataclass
class CrawledPage:
    """Represents a crawled web page."""

    url: str
    url_hash: str
    title: Optional[str]
    content_markdown: str
    content_length: int
    content_hash: str
    meta_description: Optional[str]
    http_status_code: int
    depth: int
    links: List[str] = field(default_factory=list)
    metadata: Dict[str, object] = field(default_factory=dict)


def url_hash(url: str) -> str:
    """Generate SHA-256 hash of URL."""
    return hashlib.sha256(url.encode()).hexdigest()


def content_hash(content: str) -> str:
    """Generate SHA-256 hash of content."""
    return hashlib.sha256(content.encode()).hexdigest()


class WebCrawlerService:
    """
    Single-page crawler service using crawl4ai.

    This service provides async single-page crawling with:
    - Content extraction optimized for RAG (markdown)
    - Link extraction for child page discovery
    - URL pattern filtering for include/exclude
    - Configurable browser options
    """

    def __init__(
        self,
        options: Optional[CrawlOptions] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> None:
        """
        Initialize crawler service.

        Args:
            options: Crawl configuration options
            include_patterns: URL patterns to include (glob)
            exclude_patterns: URL patterns to exclude (glob)
        """
        self.options = options or CrawlOptions()
        self.include_patterns = include_patterns or []
        self.exclude_patterns = exclude_patterns or []

    def _get_browser_config(self) -> BrowserConfig:
        """Create browser configuration."""
        config = BrowserConfig(
            headless=True,
            verbose=False,
            java_script_enabled=self.options.render_js,
            ignore_https_errors=False,
            headers=self.options.headers or {},
        )

        if self.options.user_agent:
            config.user_agent = self.options.user_agent

        return config

    def _extract_links(self, result: CrawlResult, base_url: str) -> List[str]:
        """
        Extract and normalize internal links from crawl result.

        Args:
            result: Crawl4ai result object
            base_url: Base URL for resolving relative links

        Returns:
            List of absolute URLs found on the page
        """
        links: List[str] = []
        if not result.links:
            return links

        base_domain = urlparse(base_url).netloc

        # crawl4ai can classify a same-host URL with an explicit port as
        # external. Inspect both buckets and enforce our own exact host/port.
        for link_info in result.links.get("internal", []) + result.links.get(
            "external", []
        ):
            link_url = (
                link_info.get("href")
                if isinstance(link_info, dict)
                else str(link_info)
            )
            if not link_url:
                continue

            # Resolve relative URLs
            if not link_url.startswith(("http://", "https://")):
                link_url = urljoin(base_url, link_url)

            # Parse and validate
            parsed = urlparse(link_url)

            # Skip non-http(s) URLs
            if parsed.scheme not in ("http", "https"):
                continue

            # Skip external links
            if parsed.netloc != base_domain and not self.options.follow_external_links:
                continue

            # Skip fragments and certain patterns
            if link_url.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            # Normalize: remove fragments
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                normalized += f"?{parsed.query}"

            links.append(normalized)

        return list(dict.fromkeys(links))

    def _build_crawled_page(
        self,
        result: CrawlResult,
        url: str,
        depth: int,
    ) -> CrawledPage:
        """Build CrawledPage from crawl4ai result."""
        markdown = result.markdown
        markdown_content = (
            markdown
            if isinstance(markdown, str)
            else getattr(markdown, "raw_markdown", "")
        ) or ""
        metadata = result.metadata or {}

        return CrawledPage(
            url=result.url or url,
            url_hash=url_hash(result.url or url),
            title=metadata.get("title"),
            content_markdown=markdown_content,
            content_length=len(markdown_content),
            content_hash=content_hash(markdown_content),
            meta_description=metadata.get("description"),
            http_status_code=result.status_code or 200,
            depth=depth,
            links=self._extract_links(result, result.url or url),
            metadata={
                "crawled_at": time.time(),
                "word_count": len(markdown_content.split()),
                "score": metadata.get("score"),
            },
        )

    def should_crawl_url(self, url: str) -> bool:
        """
        Check if URL should be crawled based on include/exclude patterns.

        Args:
            url: URL to check

        Returns:
            True if URL should be crawled
        """
        # Check exclude patterns first
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(url, pattern):
                return False

        # If include patterns exist, URL must match at least one
        if self.include_patterns:
            return any(fnmatch.fnmatch(url, p) for p in self.include_patterns)

        return True

    def filter_links(self, links: List[str], base_url: str) -> List[str]:
        """
        Filter discovered links based on patterns and domain.

        Args:
            links: List of discovered URLs
            base_url: Base URL to determine domain

        Returns:
            Filtered list of URLs to crawl
        """
        base_domain = urlparse(base_url).netloc
        valid_links = []

        for link in links:
            # Must be same domain
            if (
                urlparse(link).netloc != base_domain
                and not self.options.follow_external_links
            ):
                continue

            # Must pass include/exclude filters
            if not self.should_crawl_url(link):
                continue

            valid_links.append(link)

        return valid_links

    async def crawl_page(self, url: str, depth: int = 0) -> CrawledPage:
        """
        Crawl a single page using crawl4ai.

        Args:
            url: URL to crawl
            depth: Current depth from root page

        Returns:
            CrawledPage object. Raises CrawlError with a safe, specific cause.
        """
        parsed = urlparse(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise CrawlError("invalid_url")

        try:
            if self.options.delay_seconds > 0:
                await asyncio.sleep(self.options.delay_seconds)

            run_config = CrawlerRunConfig(
                word_count_threshold=1,
                remove_overlay_elements=True,
                exclude_external_links=False,
                cache_mode=CacheMode.DISABLED,
                check_robots_txt=self.options.respect_robots_txt,
                # Installed crawl4ai HTTP strategy consumes seconds; its
                # Playwright strategy consumes milliseconds. Bound both below.
                page_timeout=self.options.timeout_seconds
                * (1000 if self.options.render_js else 1),
                delay_before_return_html=(
                    self.options.wait_time if self.options.render_js else 0
                ),
            )
            browser_config = self._get_browser_config()
            strategy = None
            if not self.options.render_js:
                headers = dict(self.options.headers or {})
                headers.setdefault("User-Agent", browser_config.user_agent)
                strategy = AsyncHTTPCrawlerStrategy(
                    browser_config=HTTPCrawlerConfig(
                        headers=headers, verify_ssl=True
                    ),
                )

            crawler = AsyncWebCrawler(
                config=browser_config, crawler_strategy=strategy,
            )
            operation = None
            try:
                total_timeout = self.options.timeout_seconds + (
                    self.options.wait_time if self.options.render_js else 0
                )
                async with asyncio.timeout(total_timeout):
                    await crawler.start()
                    operation = asyncio.create_task(
                        crawler.arun(url=url, config=run_config)
                    )
                    # Playwright awaits a separate protocol callback. Cancelling
                    # that await can orphan its eventual navigation error. Keep
                    # it alive until closing our browser resolves the callback.
                    result = await asyncio.shield(operation)
            finally:
                # __aexit__ is not called if __aenter__ fails. Startup failures
                # (including a missing browser) still need to close the driver.
                await close_crawler(crawler)
                if operation is not None:
                    if not operation.done():
                        await asyncio.wait({operation}, timeout=1)
                    if not operation.done():
                        operation.cancel()
                    # A close-induced error must be observed without replacing
                    # the original timeout/cancellation of the public request.
                    await asyncio.gather(operation, return_exceptions=True)

            if not result.success or (result.status_code or 200) >= 400:
                raise classify_crawl_error(
                    result.error_message or "",
                    result.status_code,
                )
            page = self._build_crawled_page(result, url, depth)
            if not page.content_markdown.strip():
                raise CrawlError("empty_content")
            logger.info(
                "Page crawled",
                content_length=page.content_length,
                links_count=len(page.links),
                depth=depth,
            )
            return page
        except CrawlError:
            raise
        except TimeoutError as error:
            raise CrawlError("timeout") from error
        except Exception as error:
            logger.warning(
                "Page crawl failed", error_type=type(error).__name__
            )
            raise classify_crawl_error(str(error)) from error
