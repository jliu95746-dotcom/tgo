"""Base Pydantic schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(
        from_attributes=True,
        validate_assignment=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
        populate_by_name=True,
        extra=(
            'ignore'
        ),  # Ignore unknown fields from upstream services (forward-compatible)
    )


class TimestampMixin(BaseModel):
    """Mixin for timestamp fields."""

    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class SoftDeleteMixin(BaseModel):
    """Mixin for soft delete functionality."""

    deleted_at: Optional[datetime] = Field(
        default=None, description="Soft deletion timestamp"
    )


class PaginationParams(BaseModel):
    """Pagination parameters."""

    limit: int = Field(
        default=20, ge=1, le=100, description="Number of items to return"
    )
    offset: int = Field(default=0, ge=0, description="Number of items to skip")


class PaginationMetadata(BaseModel):
    """Pagination metadata for responses."""

    total: int = Field(..., description="Total number of items")
    limit: int = Field(..., description="Number of items per page")
    offset: int = Field(..., description="Number of items skipped")
    has_next: bool = Field(..., description="Whether there are more items")
    has_prev: bool = Field(..., description="Whether there are previous items")


class PaginatedResponse(BaseModel):
    """Generic paginated response."""

    data: List[Any] = Field(..., description="List of items")
    pagination: PaginationMetadata = Field(
        ..., description="Pagination metadata"
    )


class ErrorDetail(BaseModel):
    """Error detail schema."""

    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    details: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional error details"
    )


class ErrorResponse(BaseModel):
    """Error response schema."""

    error: ErrorDetail = Field(..., description="Error information")
    request_id: Optional[str] = Field(
        default=None, description="Request ID for tracking"
    )


class SuccessResponse(BaseModel):
    """Generic success response."""

    message: str = Field(..., description="Success message")
    data: Optional[Any] = Field(default=None, description="Response data")


class HealthCheckResponse(BaseModel):
    """Health check response schema."""

    status: str = Field(..., description="Health status")
    timestamp: datetime = Field(..., description="Check timestamp")
    version: str = Field(..., description="Application version")
    database: bool = Field(..., description="Database connectivity status")


class BulkOperationResponse(BaseModel):
    """Bulk operation response schema."""

    total: int = Field(..., description="Total number of items processed")
    successful: int = Field(..., description="Number of successful operations")
    failed: int = Field(..., description="Number of failed operations")
    errors: List[ErrorDetail] = Field(
        default_factory=list, description="List of errors"
    )


class SearchParams(BaseModel):
    """Search parameters."""

    search: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Search query string",
    )
    sort_by: Optional[str] = Field(
        default=None, description="Field to sort by"
    )
    sort_order: Optional[str] = Field(
        default="asc",
        pattern="^(asc|desc)$",
        description="Sort order: asc or desc",
    )
