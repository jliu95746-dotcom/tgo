"""Schemas for unified search across visitors and chat messages."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field, computed_field

from app.schemas.base import BaseSchema
from app.schemas.visitor import VisitorBasicResponse


class SearchScope(str, Enum):
    """Supported search scopes."""

    ALL = "all"
    VISITORS = "visitors"
    MESSAGES = "messages"


class MessageSearchResult(BaseSchema):
    """Normalized result item for message search."""

    message_id: Optional[int] = Field(None, description="Global message identifier")
    client_msg_no: Optional[str] = Field(None, description="Client message number")
    message_seq: Optional[int] = Field(None, description="Sequence number within channel")
    from_uid: Optional[str] = Field(None, description="Sender WuKongIM UID")
    channel_id: Optional[str] = Field(None, description="Channel identifier")
    channel_type: Optional[int] = Field(None, description="Channel type code")
    timestamp: Optional[int] = Field(None, description="Unix timestamp (seconds)")
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Decoded message payload (base64 decoded when possible)",
    )
    topic: Optional[str] = Field(None, description="Message topic when available")
    preview_text: Optional[str] = Field(
        None,
        description="Short preview text extracted from payload",
    )

    @computed_field  # type: ignore[misc]
    @property
    def message_id_str(self) -> Optional[str]:
        """String representation of message_id for front-end compatibility."""
        if self.message_id is None:
            return None
        try:
            return str(self.message_id)
        except Exception:
            return None


class SearchPagination(BaseSchema):
    """Pagination metadata for search results."""

    page: int = Field(..., ge=1, description="Current page (1-indexed)")
    page_size: int = Field(..., ge=1, description="Number of items per page")
    total: int = Field(..., ge=0, description="Total number of matching records")

    @computed_field  # type: ignore[misc]
    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total

    @computed_field  # type: ignore[misc]
    @property
    def has_previous(self) -> bool:
        return self.page > 1 and self.total > 0


class MessageSearchPagination(BaseSchema):
    """Only authorized records contribute to message pagination."""

    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)
    total: Optional[int] = Field(
        None, ge=0,
        description="Exact authorized total, only known after search exhaustion",
    )
    has_next: bool
    has_previous: bool


class UnifiedSearchResponse(BaseSchema):
    """Response payload for unified visitor/message search."""

    query: str = Field(..., description="Search keyword used")
    scope: SearchScope = Field(..., description="Scope that was applied")
    visitors: List[VisitorBasicResponse] = Field(
        default_factory=list,
        description="Visitor records that matched the query",
    )
    messages: List[MessageSearchResult] = Field(
        default_factory=list,
        description="Messages that matched the query",
    )
    visitor_count: int = Field(..., description="Number of visitor entries returned")
    message_count: int = Field(..., description="Number of message entries returned")
    visitor_pagination: Optional[SearchPagination] = Field(
        None, description="Pagination metadata for visitor results"
    )
    message_pagination: Optional[MessageSearchPagination] = Field(
        None, description="Pagination metadata for message results"
    )
