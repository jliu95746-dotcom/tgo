"""Typed monitoring values with explicit measurement scope and unavailable state."""

from typing import Literal

from pydantic import BaseModel, Field


class DependencyCheck(BaseModel):
    status: Literal["healthy", "unhealthy"]
    response_time_ms: float = Field(ge=0)
    error: str | None = None


class HealthChecks(BaseModel):
    database: DependencyCheck
    redis: DependencyCheck
    vector_db: DependencyCheck
    total_check_time_ms: float = Field(ge=0)

    @property
    def healthy(self) -> bool:
        return all(
            item.status == "healthy"
            for item in (self.database, self.redis, self.vector_db)
        )


class RequestMetricsValues(BaseModel):
    requests_total: int | None = Field(
        default=None,
        description="This worker's completed HTTP requests, excluding probes",
    )
    requests_per_second: float | None = Field(
        default=None, description="Mean request rate since collector initialization"
    )
    response_time_p95: float | None = Field(
        default=None,
        description=(
            "Milliseconds; nearest-rank p95 of the latest 1000 requests, "
            "including streamed response bodies"
        ),
    )
    latency_samples: int | None = None
    active_requests: int | None = None
    errors_total: int | None = Field(
        default=None,
        description="This worker's HTTP status >=400 or incomplete responses",
    )
    uptime_seconds: float | None = Field(
        default=None, description="Seconds since this worker's collector initialized"
    )
    # Legacy uninstrumented cumulative counters stay null, never made-up numbers.
    active_connections: int | None = None
    documents_processed: int | None = None
    embeddings_generated: int | None = None
    database_connections: int | None = None
    redis_connections: int | None = None
    vector_db_operations: int | None = None
    file_uploads_total: int | None = None
    search_queries_total: int | None = None


class KnowledgeCounts(BaseModel):
    status: Literal["available", "unavailable", "disabled"] = "unavailable"
    scope: Literal["rag_database_snapshot"] = "rag_database_snapshot"
    collections_total: int | None = None
    files_total: int | None = None
    files_completed: int | None = None
    files_pending: int | None = None
    files_processing: int | None = None
    files_failed: int | None = None
    documents_total: int | None = Field(
        default=None,
        description=(
            "Stored chunks/QA outside soft-deleted parents, "
            "not lifetime processing events"
        ),
    )
    embeddings_total: int | None = Field(
        default=None,
        description="Stored embedded documents, not lifetime generation events",
    )
    error: str | None = None
