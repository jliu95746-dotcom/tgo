"""
Common Pydantic schemas used across the application.
"""

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field
from .observability import HealthChecks, KnowledgeCounts, RequestMetricsValues


class ErrorResponse(BaseModel):
    """Standard error response schema."""
    
    error: Dict[str, Any] = Field(
        ...,
        description="Error details",
        examples=[
            {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request data",
                "details": {"field": "name", "issue": "Field is required"}
            }
        ]
    )
    request_id: Optional[str] = Field(
        None,
        description="Unique request identifier for tracking"
    )


class PaginationParams(BaseModel):
    """Pagination parameters for list endpoints."""
    
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Number of items to return"
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of items to skip"
    )


class PaginationMetadata(BaseModel):
    """Pagination metadata for list responses."""
    
    total: int = Field(
        ...,
        ge=0,
        description="Total number of items available"
    )
    limit: int = Field(
        ...,
        ge=1,
        description="Number of items requested"
    )
    offset: int = Field(
        ...,
        ge=0,
        description="Number of items skipped"
    )
    has_next: bool = Field(
        ...,
        description="Whether there are more items available"
    )
    has_prev: bool = Field(
        ...,
        description="Whether there are previous items available"
    )


class HealthResponse(BaseModel):
    """Health check response schema."""
    
    status: str = Field(
        ...,
        description="Overall health status",
        examples=["healthy", "unhealthy", "degraded"]
    )
    version: str = Field(
        ...,
        description="Application version"
    )
    timestamp: str = Field(
        ...,
        description="Health check timestamp (ISO format)"
    )
    checks: HealthChecks = Field(
        ...,
        description="Individual health check results",
        examples=[
            {
                "database": {"status": "healthy", "response_time_ms": 15},
                "redis": {"status": "healthy", "response_time_ms": 5},
                "vector_db": {"status": "healthy", "response_time_ms": 25}
            }
        ]
    )


class MetricsResponse(BaseModel):
    """Metrics response schema."""
    
    enabled: bool = True
    scope: Literal["current_api_worker"] = "current_api_worker"
    knowledge: KnowledgeCounts = Field(default_factory=lambda: KnowledgeCounts())
    unavailable_metrics: list[str] = Field(default_factory=list)
    metrics: RequestMetricsValues = Field(
        ...,
        description="Application metrics",
        examples=[
            {
                "requests_total": 1234,
                "requests_per_second": 12.5,
                "response_time_p95": 150.0,
                "active_connections": 25,
                "documents_processed": 5678,
                "embeddings_generated": 9012
            }
        ]
    )
    timestamp: str = Field(
        ...,
        description="Metrics collection timestamp (ISO format)"
    )


class StatusResponse(BaseModel):
    """Generic status response schema."""
    
    status: str = Field(
        ...,
        description="Operation status",
        examples=["success", "pending", "failed"]
    )
    message: Optional[str] = Field(
        None,
        description="Status message"
    )
    details: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional status details"
    )
