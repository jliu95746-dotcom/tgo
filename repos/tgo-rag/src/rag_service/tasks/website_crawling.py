"""
Website crawling tasks for RAG document generation.

This module provides Celery tasks for single-page crawling with hierarchical
page discovery. Each page is crawled independently, with child pages being
created and queued as new tasks.

Design:
- crawl_page_task: Crawls a single page, creates File, discovers child pages
- Each page tracks parent_page_id for tree structure
- Crawl configuration is stored in Collection.crawl_config

Performance optimizations:
- Batch child page creation with single transaction
- Minimize database round-trips by caching page counts
- Use batch task queueing for child pages
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from urllib.parse import urlparse

from sqlalchemy import select

from .celery_app import celery_app
from ..config import get_settings
from ..database import get_db_session, reset_db_state
from ..logging_config import get_logger
from ..models import Collection, File, WebsitePage
from ..services.crawler import CrawlOptions, WebCrawlerService
from ..services.crawl_errors import CrawlError
from ..services.website_discovery import reserve_child_pages
from ..services.website_dispatch import (
    fail_website_dispatch,
    prepare_website_dispatch,
)

logger = get_logger(__name__)
settings = get_settings()


def create_crawl_event_loop() -> asyncio.AbstractEventLoop:
    """Browser subprocesses require Proactor on Windows, regardless of policy."""
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.new_event_loop()


@dataclass
class PageInfo:
    """Container for page information used during crawling."""
    id: UUID
    url: str
    depth: int
    collection_id: UUID
    project_id: UUID
    crawl_config: Optional[Dict[str, Any]]


@dataclass
class CrawlConfig:
    """Parsed crawl configuration with defaults."""
    max_depth: int
    max_pages: int
    include_patterns: List[str]
    exclude_patterns: List[str]
    render_js: bool
    delay_seconds: float
    user_agent: Optional[str]
    timeout_seconds: int
    respect_robots_txt: bool
    headers: Optional[Dict[str, str]]
    wait_time: float
    follow_external_links: bool
    headers_origin: Optional[str]

    @classmethod
    def from_dict(cls, config: Dict[str, Any], max_depth_override: Optional[int] = None) -> "CrawlConfig":
        """Create CrawlConfig from dictionary with defaults."""
        return cls(
            max_depth=max_depth_override if max_depth_override is not None else config.get("max_depth", 3),
            max_pages=config.get("max_pages", 100),
            include_patterns=config.get("include_patterns", []),
            exclude_patterns=config.get("exclude_patterns", []),
            render_js=config.get("render_js", config.get("js_rendering", False)),
            delay_seconds=config.get(
                "delay_seconds", config.get("delay_between_requests", 1.0),
            ),
            user_agent=config.get("user_agent"),
            timeout_seconds=config.get("timeout_seconds", config.get("timeout", 30)),
            respect_robots_txt=config.get("respect_robots_txt", True),
            headers=config.get("headers"),
            wait_time=config.get("wait_time", 0),
            follow_external_links=config.get("follow_external_links", False),
            headers_origin=config.get("headers_origin"),
        )


def merge_crawl_configs(
    collection_config: Optional[Dict[str, Any]],
    page_config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Merge crawl configurations with page-level config taking priority.

    Args:
        collection_config: Base configuration from Collection.crawl_config
        page_config: Override configuration from WebsitePage.crawl_config

    Returns:
        Merged configuration dictionary where page_config values override
        collection_config values for the same keys.
    """
    return {**(collection_config or {}), **(page_config or {})}


async def _load_page_and_config(
    page_id: UUID,
    max_depth_override: Optional[int] = None,
) -> tuple[Optional[PageInfo], Optional[CrawlConfig], Optional[str]]:
    """
    Load page and collection configuration from database.

    Returns:
        Tuple of (page_info, crawl_config, error_message)
        If error_message is set, page_info and crawl_config will be None.
    """
    async with get_db_session() as db:
        result = await db.execute(
            select(WebsitePage).join(
                Collection, Collection.id == WebsitePage.collection_id,
            ).where(
                WebsitePage.id == page_id,
                Collection.project_id == WebsitePage.project_id,
                Collection.deleted_at.is_(None),
            ).with_for_update(of=(Collection, WebsitePage))
        )
        page = result.scalar_one_or_none()

        if not page:
            return None, None, "Page not found"

        if page.status not in ("pending", "retry"):
            return None, None, f"invalid_status: {page.status}"

        # Load collection for configuration
        coll_result = await db.execute(
            select(Collection).where(
                Collection.id == page.collection_id,
                Collection.project_id == page.project_id,
                Collection.deleted_at.is_(None),
            )
        )
        collection = coll_result.scalar_one_or_none()

        if not collection:
            return None, None, "Collection not found"
        from ..services.company_resources import require_processing
        await require_processing(page.project_id)

        # Merge and parse crawl configurations
        merged_config = merge_crawl_configs(collection.crawl_config, page.crawl_config)
        if merged_config.get("headers") and not merged_config.get("headers_origin"):
            merged_config["headers_origin"] = (
                (collection.crawl_config or {}).get("start_url") or page.url
            )
        crawl_config = CrawlConfig.from_dict(merged_config, max_depth_override)

        page_info = PageInfo(
            id=page.id,
            url=page.url,
            depth=page.depth,
            collection_id=page.collection_id,
            project_id=page.project_id,
            crawl_config=page.crawl_config,
        )

        # Update status to crawling
        page.status = "crawling"
        await db.commit()

    return page_info, crawl_config, None


async def _update_page_status(
    page_id: UUID,
    status: str,
    error_message: Optional[str] = None,
    http_status_code: Optional[int] = None,
    expected_status: Optional[str] = None,
) -> None:
    """Update page status in database."""
    async with get_db_session() as db:
        query = select(WebsitePage).where(WebsitePage.id == page_id)
        if expected_status is not None:
            query = query.where(WebsitePage.status == expected_status)
        result = await db.execute(query.with_for_update())
        page = result.scalar_one_or_none()
        if page:
            page.status = status
            page.error_message = error_message
            if http_status_code is not None:
                page.http_status_code = http_status_code
            await db.commit()


async def crawl_page_async(
    page_id: UUID,
    auto_discover: bool = True,
    max_depth: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Async function to crawl a single page.

    This function handles:
    1. Load page and collection configuration
    2. Crawl the page content
    3. Save content and create File record
    4. Discover and create child pages (if auto_discover)
    5. Trigger document processing
    6. Update page status

    Args:
        page_id: UUID of the page to crawl
        auto_discover: Whether to automatically create child page tasks
        max_depth: Override max depth (uses collection config if None)

    Returns:
        Dictionary containing crawl results
    """
    persisted_file: Optional[File] = None
    try:
        # Step 1: Load page and collection info
        page_info, crawl_config, error = await _load_page_and_config(page_id, max_depth)

        if error:
            if error.startswith("invalid_status"):
                logger.warning(f"Page {page_id} {error}, skipping")
                return {"page_id": str(page_id), "status": "skipped", "reason": error}
            logger.error(f"Page {page_id}: {error}")
            return {"page_id": str(page_id), "status": "failed", "error": error}

        # Step 2: Check depth limit
        if page_info.depth > crawl_config.max_depth:
            await _update_page_status(
                page_id, "skipped",
                f"Exceeded max depth: {page_info.depth} > {crawl_config.max_depth}"
            )
            return {"page_id": str(page_id), "status": "skipped", "reason": "max_depth_exceeded"}

        # Step 3: Initialize crawler and crawl the page
        # An external descendant must not receive the source site's custom headers.
        request_headers = crawl_config.headers
        if crawl_config.headers_origin:
            header_origin = urlparse(crawl_config.headers_origin)
            request_origin = urlparse(page_info.url)
            if (header_origin.scheme, header_origin.netloc.lower()) != (
                request_origin.scheme, request_origin.netloc.lower(),
            ):
                request_headers = None
        options = CrawlOptions(
            render_js=crawl_config.render_js,
            delay_seconds=crawl_config.delay_seconds,
            user_agent=crawl_config.user_agent,
            timeout_seconds=crawl_config.timeout_seconds,
            respect_robots_txt=crawl_config.respect_robots_txt,
            headers=request_headers,
            wait_time=crawl_config.wait_time,
            follow_external_links=crawl_config.follow_external_links,
        )

        crawler = WebCrawlerService(
            options=options,
            include_patterns=crawl_config.include_patterns,
            exclude_patterns=crawl_config.exclude_patterns,
        )

        crawled_page = await crawler.crawl_page(page_info.url, depth=page_info.depth)

        # Handle crawl failure
        if not crawled_page:
            await _update_page_status(page_id, "failed", "Crawl returned empty result")
            return {"page_id": str(page_id), "status": "failed", "error": "empty_result"}

        # Step 4: Save crawl results and create File
        file_id = None
        async with get_db_session() as db:
            from ..services.company_resources import ensure_capacity
            await ensure_capacity(db, page_info.project_id, len((crawled_page.content_markdown or "").encode("utf-8")))
            result = await db.execute(select(WebsitePage).join(
                Collection, Collection.id == WebsitePage.collection_id,
            ).where(
                WebsitePage.id == page_id,
                WebsitePage.project_id == page_info.project_id,
                WebsitePage.status == "crawling",
                Collection.project_id == page_info.project_id,
                Collection.deleted_at.is_(None),
            ).with_for_update(of=(Collection, WebsitePage)))
            page = result.scalar_one_or_none()
            if page is None:
                return {"page_id": str(page_id), "status": "skipped",
                        "reason": "Page or collection changed during fetch"}

            # Update page with crawled content
            page.title = crawled_page.title
            page.content_markdown = crawled_page.content_markdown
            page.content_length = crawled_page.content_length
            page.content_hash = crawled_page.content_hash
            page.meta_description = crawled_page.meta_description
            page.http_status_code = crawled_page.http_status_code
            page.discovered_links = [
                {"url": link, "created": False}
                for link in crawled_page.links
            ]
            page.status = "fetched"

            # Create File record if content exists
            if crawled_page.content_markdown and crawled_page.content_length > 0:
                file_id = uuid4()
                safe_title = (crawled_page.title or "page")[:100].replace("/", "_").replace("\\", "_")
                filename = f"{safe_title}_{file_id}.md"
                storage_path = os.path.join(settings.upload_dir, str(file_id))

                os.makedirs(os.path.dirname(storage_path) if os.path.dirname(storage_path) else settings.upload_dir, exist_ok=True)
                with open(storage_path, "w", encoding="utf-8") as f:
                    f.write(crawled_page.content_markdown)

                file_record = File(
                    id=file_id,
                    project_id=page_info.project_id,
                    collection_id=page_info.collection_id,
                    original_filename=filename,
                    file_size=len(crawled_page.content_markdown.encode("utf-8")),
                    content_type="text/markdown",
                    storage_provider="local",
                    storage_path=storage_path,
                    storage_metadata={
                        "source": "website_crawl",
                        "source_url": page_info.url,
                        "page_id": str(page_id),
                    },
                    status="pending",
                    description=crawled_page.meta_description,
                )
                db.add(file_record)

                page.file_id = file_id
                page.status = "extracted"

            await db.commit()
            if file_id is not None:
                persisted_file = file_record

        # Step 5: Discover and create child pages (optimized batch processing)
        children_created = 0
        max_pages_reached = False
        created_child_ids: List[UUID] = []

        if auto_discover and page_info.depth < crawl_config.max_depth:
            # Filter links first (no DB access needed)
            valid_links = crawler.filter_links(crawled_page.links, page_info.url)

            if valid_links:
                # Every creator uses the same collection lock and quota rules.
                async with get_db_session() as db:
                    reserved = await reserve_child_pages(
                        db, page_id, page_info.project_id, valid_links,
                        crawl_config.max_depth,
                    )
                    created_child_ids = reserved.page_ids
                    children_created = len(created_child_ids)
                    max_pages_reached = reserved.limit_reached
                    await db.commit()

                # Queue child tasks outside the DB session
                for child_id in created_child_ids:
                    try:
                        crawl_page_task.delay(
                            str(child_id), auto_discover=True,
                            max_depth=crawl_config.max_depth,
                        )
                    except Exception as error:
                        logger.warning("Child crawl dispatch failed",
                                       error_type=type(error).__name__)
                        await _update_page_status(
                            child_id, "failed", "抓取任务未能提交，请稍后重新抓取。",
                            expected_status="pending",
                        )

        # Step 6: Trigger document processing
        if persisted_file is not None:
            from .document_processing import process_file_task
            if not await prepare_website_dispatch(persisted_file):
                return {"page_id": str(page_id), "status": "skipped",
                        "reason": "Page or file changed before document dispatch"}
            try:
                process_file_task.delay(str(file_id), str(page_info.collection_id))
            except Exception as error:
                logger.warning("Website document dispatch failed",
                               page_id=str(page_id), error_type=type(error).__name__)
                safe_error = "知识库处理任务未能提交，请稍后重新抓取。"
                failed = await fail_website_dispatch(persisted_file, safe_error)
                if failed:
                    return {"page_id": str(page_id), "status": "failed",
                            "error": safe_error, "error_code": "dispatch_failed"}
                return {"page_id": str(page_id), "status": "skipped",
                        "reason": "Document was claimed or source changed"}

        logger.info(
            f"Crawled page {page_id}: {crawled_page.content_length} chars, "
            f"{len(crawled_page.links)} links, {children_created} children created"
            f"{' (max_pages reached)' if max_pages_reached else ''}"
        )

        return {
            "page_id": str(page_id),
            "url": page_info.url,
            "status": "success",
            "content_length": crawled_page.content_length,
            "links_discovered": len(crawled_page.links),
            "children_created": children_created,
            "file_id": str(file_id) if file_id else None,
            "max_pages_reached": max_pages_reached,
        }

    except CrawlError as error:
        await _update_page_status(
            page_id, "failed", str(error),
            http_status_code=error.http_status_code,
        )
        return {
            "page_id": str(page_id), "status": "failed",
            "error": str(error), "error_code": error.code,
        }
    except Exception as e:
        logger.error("Page crawl task failed", page_id=str(page_id),
                     error_type=type(e).__name__)
        safe_error = f"网页处理失败（{type(e).__name__}），请检查后台日志后重试。"
        if persisted_file is not None:
            await fail_website_dispatch(persisted_file, safe_error)
        else:
            await _update_page_status(
                page_id, "failed", safe_error, expected_status="crawling",
            )
        return {
            "page_id": str(page_id),
            "status": "failed",
            "error": safe_error,
        }


@celery_app.task(bind=True, name="crawl_page_task")
def crawl_page_task(
    self,
    page_id: str,
    auto_discover: bool = True,
    max_depth: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Celery task for crawling a single page.

    This task handles:
    1. Crawl the page content using crawl4ai
    2. Save content and create File record
    3. Discover child links and create child page records
    4. Queue child pages for crawling (if auto_discover)
    5. Trigger document processing

    Args:
        page_id: UUID string of the page to crawl
        auto_discover: Whether to automatically discover and crawl child pages
        max_depth: Override max crawl depth (uses collection config if None)

    Returns:
        Dictionary containing crawl results
    """
    try:
        page_uuid = UUID(page_id)

        # Reset database state for Celery worker
        reset_db_state()

        # Run async crawling
        loop = create_crawl_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(
                crawl_page_async(
                    page_id=page_uuid,
                    auto_discover=auto_discover,
                    max_depth=max_depth,
                )
            )
            return result
        finally:
            # Clean up database connections before closing the loop
            reset_db_state()
            loop.close()

    except Exception as e:
        logger.error(f"Crawl page task failed for {page_id}: {e}")
        return {
            "page_id": page_id,
            "status": "failed",
            "error": str(e),
        }


