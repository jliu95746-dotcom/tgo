"""
Search-related Pydantic schemas.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """Schema for individual search results."""

    document_id: UUID = Field(
        ...,
        description="Document unique identifier"
    )
    file_id: Optional[UUID] = Field(
        None,
        description="Associated file ID (None for QA pairs)"
    )
    collection_id: Optional[UUID] = Field(
        None,
        description="Associated collection ID"
    )
    relevance_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Relevance score (0-1, higher is more relevant)"
    )
    content_preview: str = Field(
        ...,
        description="Preview of the document content"
    )
    document_title: Optional[str] = Field(
        None,
        description="Document title or heading"
    )
    content_type: str = Field(
        ...,
        description="Type of content",
        examples=["paragraph", "heading", "table", "list", "code", "image", "metadata"]
    )
    chunk_index: Optional[int] = Field(
        None,
        description="Index of this chunk within the document"
    )
    page_number: Optional[int] = Field(
        None,
        description="Page number in original document"
    )
    section_title: Optional[str] = Field(
        None,
        description="Section or chapter title"
    )
    tags: Optional[Dict[str, Any]] = Field(
        None,
        description="Document tags and metadata"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional metadata including source info"
    )
    created_at: datetime = Field(
        ...,
        description="Document creation timestamp"
    )


class SearchMetadata(BaseModel):
    """Schema for search metadata."""
    
    query: str = Field(
        ...,
        description="Original search query"
    )
    total_results: int = Field(
        ...,
        ge=0,
        description="Total number of results found"
    )
    returned_results: int = Field(
        ...,
        ge=0,
        description="Number of results returned in this response"
    )
    search_time_ms: int = Field(
        ...,
        ge=0,
        description="Search execution time in milliseconds"
    )
    filters_applied: Optional[Dict[str, Any]] = Field(
        None,
        description="Filters that were applied to the search"
    )
    search_type: str = Field(
        default="semantic",
        description="Type of search performed",
        examples=["semantic", "keyword", "hybrid"]
    )


class SearchResponse(BaseModel):
    """Schema for search responses."""
    
    results: List[SearchResult] = Field(
        ...,
        description="List of search results"
    )
    search_metadata: SearchMetadata = Field(
        ...,
        description="Search execution metadata"
    )


class AutomaticAnswerSearchResult(SearchResult):
    """Governed chunk body for generation, distinct from the UI preview."""

    content: str = Field(..., min_length=1, description="Full admitted chunk content")


class AutomaticAnswerSearchResponse(BaseModel):
    """Automatic-answer results must retain their full admitted content."""

    results: List[AutomaticAnswerSearchResult]
    search_metadata: SearchMetadata
