"""
Website crawling models for RAG processing.

This module provides database models for managing website pages
for RAG document generation.

Design: Hierarchical page structure with parent-child relationships
- Each page tracks its parent_page_id for tree traversal
- Supports incremental crawling from any page
- Depth is tracked explicitly for efficient querying
- Crawl configuration is stored in Collection.crawl_config
"""

from typing import TYPE_CHECKING, Any, List, Optional
from uuid import UUID as PyUUID

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .collections import Collection
    from .files import File


class WebsitePage(Base, UUIDMixin, TimestampMixin):
    """
    Website page model for storing crawled page content.

    This model stores individual web pages crawled from a website,
    including their content and metadata for RAG processing.

    Supports hierarchical structure via parent_page_id for:
    - Tree traversal (find children, ancestors)
    - Incremental deep crawling from any page
    - Tracking link discovery source
    """

    __tablename__ = "rag_website_pages"

    # Foreign keys
    collection_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rag_collections.id", ondelete="CASCADE"),
        nullable=False,
        doc="Associated collection ID",
    )

    project_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        doc="Associated project ID",
    )

    file_id: Mapped[Optional[PyUUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rag_files.id", ondelete="SET NULL"),
        nullable=True,
        doc="Associated file ID (created after processing)",
    )

    # Parent page relationship (self-referential)
    parent_page_id: Mapped[Optional[PyUUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rag_website_pages.id", ondelete="SET NULL"),
        nullable=True,
        doc="Parent page ID (NULL for root pages)",
    )

    # Page information
    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        doc="Page URL",
    )

    url_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="SHA-256 hash of URL for deduplication",
    )

    title: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        doc="Page title",
    )

    depth: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Crawl depth from root page",
    )

    # Crawl source tracking
    crawl_source: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="discovered",
        doc=(
            "How this page was added: 'initial', 'discovered', 'manual',"
            " 'deep_crawl'"
        ),
    )

    # Content
    content_markdown: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Extracted content in Markdown format",
    )

    content_length: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Content length in characters",
    )

    content_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        doc="SHA-256 hash of content for deduplication",
    )

    # Discovered links (stored for future deep crawling)
    discovered_links: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(
        JSONB,
        nullable=True,
        doc="Links found on this page: [{url, text, created}]",
    )

    # Per-page crawl configuration (overrides Collection.crawl_config)
    crawl_config: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        doc=(
            "Page-specific crawl configuration that overrides collection"
            " defaults"
        ),
    )

    # Metadata
    meta_description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Page meta description",
    )

    page_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        doc="Additional page metadata (headers, images, etc.)",
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="pending",
        doc=(
            "Page status: pending, crawling, fetched, extracted, processing,"
            " processed, failed, skipped"
        ),
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Error message if processing failed",
    )

    # HTTP response info
    http_status_code: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        doc="HTTP response status code",
    )

    # Relationships
    collection: Mapped["Collection"] = relationship(
        "Collection",
        doc="Associated collection",
    )

    file: Mapped[Optional["File"]] = relationship(
        "File",
        doc="Associated file record",
    )

    # Self-referential relationship for parent-child pages
    parent_page: Mapped[Optional["WebsitePage"]] = relationship(
        "WebsitePage",
        back_populates="children",
        remote_side="WebsitePage.id",
        doc="Parent page",
    )

    children: Mapped[List["WebsitePage"]] = relationship(
        "WebsitePage",
        back_populates="parent_page",
        doc="Child pages",
    )

    # Indexes
    __table_args__ = (
        Index("idx_website_pages_collection_id", "collection_id"),
        Index("idx_website_pages_project_id", "project_id"),
        Index("idx_website_pages_file_id", "file_id"),
        Index("idx_website_pages_parent_page_id", "parent_page_id"),
        Index("idx_website_pages_url_hash", "url_hash"),
        Index("idx_website_pages_status", "status"),
        Index("idx_website_pages_depth", "depth"),
        Index("idx_website_pages_crawl_source", "crawl_source"),
        Index("idx_website_pages_created_at", "created_at"),
        # Unique constraint on URL within a collection
        Index(
            "idx_website_pages_collection_url",
            "collection_id",
            "url_hash",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<WebsitePage(id={self.id}, url='{self.url[:50]}...',"
            f" depth={self.depth}, status='{self.status}')>"
        )
