"""
Website crawling Pydantic schemas.

This module provides request/response schemas for website page APIs.
Crawl configuration is stored in Collection.crawl_config.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


# ============================================================================
# Crawl Configuration Schemas (stored in Collection.crawl_config)
# ============================================================================

class CrawlOptionsSchema(BaseModel):
    """Schema for crawl configuration options."""

    render_js: bool = Field(
        default=False,
        description="Whether to render JavaScript (uses headless browser)",
    )
    respect_robots_txt: bool = Field(
        default=True,
        description="Whether to respect robots.txt rules",
    )
    delay_seconds: float = Field(
        default=1.0,
        ge=0,
        le=60,
        description="Delay between requests in seconds",
    )
    user_agent: Optional[str] = Field(
        default=None,
        description="Custom user agent string",
    )
    timeout_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Request timeout in seconds",
    )
    headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Custom HTTP headers",
    )
    wait_time: float = Field(
        default=0, ge=0, le=30,
        description="Wait after page load before extracting dynamic content",
    )
    follow_external_links: bool = Field(
        default=False, description="Whether to follow links to other origins",
    )


# ============================================================================
# Page Request/Response Schemas
# ============================================================================

class AddPageRequest(BaseModel):
    """Schema for adding a page to crawl."""

    url: HttpUrl = Field(
        ...,
        description="URL of the page to add",
        examples=["https://docs.python.org/3/"],
    )
    parent_page_id: Optional[UUID] = Field(
        default=None,
        description="Parent page ID. If provided, the new page will be a child of this page with depth = parent.depth + 1",
    )
    max_depth: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Maximum crawl depth from this page (0 = only this page)",
    )
    include_patterns: Optional[List[str]] = Field(
        default=None,
        description="URL patterns to include (glob patterns)",
    )
    exclude_patterns: Optional[List[str]] = Field(
        default=None,
        description="URL patterns to exclude (glob patterns)",
    )
    options: Optional[CrawlOptionsSchema] = Field(
        default=None,
        description="Crawl options (overrides collection defaults)",
    )


class AddPageResponse(BaseModel):
    """Schema for add page response."""

    success: bool = Field(
        ...,
        description="Whether the page was added successfully",
    )
    page_id: Optional[UUID] = Field(
        None,
        description="ID of the newly created page (if added)",
    )
    message: str = Field(
        ...,
        description="Status message",
    )
    status: str = Field(
        ...,
        description="Result status: 'added', 'exists', 'crawling'",
    )


class WebsitePageResponse(BaseModel):
    """Schema for website page API responses."""

    id: UUID = Field(
        ...,
        description="Page unique identifier",
    )
    collection_id: UUID = Field(
        ...,
        description="Associated collection ID",
    )
    parent_page_id: Optional[UUID] = Field(
        None,
        description="Parent page ID (for hierarchical structure)",
    )
    url: str = Field(
        ...,
        description="Page URL",
    )
    title: Optional[str] = Field(
        None,
        description="Page title",
    )
    depth: int = Field(
        ...,
        description="Crawl depth from root page",
    )
    content_length: int = Field(
        ...,
        description="Content length in characters",
    )
    meta_description: Optional[str] = Field(
        None,
        description="Page meta description",
    )
    status: str = Field(
        ...,
        description="Page status: pending, crawling, fetched, extracted, processing, processed, failed, skipped",
    )
    crawl_source: Optional[str] = Field(
        None,
        description="How this page was added: initial, discovered, manual, deep_crawl",
    )
    http_status_code: Optional[int] = Field(
        None,
        description="HTTP response status code",
    )
    file_id: Optional[UUID] = Field(
        None,
        description="Associated file ID (after processing)",
    )
    discovered_links: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Links discovered on this page",
    )
    error_message: Optional[str] = Field(
        None,
        description="Error message if processing failed",
    )
    tree_completed: bool = Field(
        default=False,
        description="Whether this page and all its descendant pages have been processed or skipped",
    )
    has_children: bool = Field(
        default=False,
        description="Whether this page has any child pages",
    )
    children: Optional[List["WebsitePageResponse"]] = Field(
        default=None,
        description="Child pages (populated when tree_depth > 0)",
    )
    created_at: datetime = Field(
        ...,
        description="Page creation timestamp",
    )
    updated_at: datetime = Field(
        ...,
        description="Page last update timestamp",
    )

    class Config:
        from_attributes = True


# Rebuild model to support recursive self-reference
WebsitePageResponse.model_rebuild()


class WebsitePageListResponse(BaseModel):
    """Schema for paginated page list responses."""

    data: List[WebsitePageResponse] = Field(
        ...,
        description="List of pages",
    )
    pagination: "PaginationMetadata" = Field(
        ...,
        description="Pagination metadata",
    )


# ============================================================================
# Deep Crawl Schemas
# ============================================================================

class CrawlDeeperRequest(BaseModel):
    """Schema for deep crawl request from an existing page."""

    max_depth: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum crawl depth from this page",
    )
    include_patterns: Optional[List[str]] = Field(
        default=None,
        description="URL patterns to include (fnmatch style)",
    )
    exclude_patterns: Optional[List[str]] = Field(
        default=None,
        description="URL patterns to exclude (fnmatch style)",
    )


class CrawlDeeperResponse(BaseModel):
    """Schema for deep crawl response."""

    success: bool = Field(
        ...,
        description="Whether the operation was successful",
    )
    source_page_id: UUID = Field(
        ...,
        description="ID of the source page",
    )
    pages_added: int = Field(
        ...,
        description="Number of new pages added to crawl queue",
    )
    pages_skipped: int = Field(
        ...,
        description="Number of pages skipped (already exists or crawling)",
    )
    links_found: int = Field(
        ...,
        description="Total number of links found in the page",
    )
    message: str = Field(
        ...,
        description="Status message",
    )
    added_urls: List[str] = Field(
        default_factory=list,
        description="URLs that were added to crawl queue",
    )


# ============================================================================
# Collection Progress Schema
# ============================================================================

class CrawlProgressSchema(BaseModel):
    """Schema for collection crawl progress (computed from pages)."""

    total_pages: int = Field(
        ...,
        ge=0,
        description="Total number of pages in collection",
    )
    pages_pending: int = Field(
        ...,
        ge=0,
        description="Number of pages pending crawl",
    )
    pages_crawled: int = Field(
        ...,
        ge=0,
        description="Number of pages successfully crawled",
    )
    pages_processed: int = Field(
        ...,
        ge=0,
        description="Number of pages processed into documents",
    )
    pages_processing: int = Field(
        ...,
        ge=0,
        description="Number of pages currently being processed",
    )
    pages_failed: int = Field(
        ...,
        ge=0,
        description="Number of pages that failed to process",
    )
    progress_percent: float = Field(
        ...,
        ge=0,
        le=100,
        description="Overall progress percentage",
    )


# Import for pagination - avoid circular imports
from .common import PaginationMetadata
WebsitePageListResponse.model_rebuild()

