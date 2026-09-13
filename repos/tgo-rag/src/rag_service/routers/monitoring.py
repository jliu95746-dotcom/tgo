"""
Monitoring and metrics endpoints.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest

from ..config import get_settings
from ..schemas.common import MetricsResponse
from ..schemas.observability import KnowledgeCounts, RequestMetricsValues
from ..services.knowledge_metrics import collect_knowledge_counts
from ..services.request_metrics import request_metrics

router = APIRouter()


@router.get("/metrics", response_model=None)
async def prometheus_metrics() -> Response | dict[str, str]:
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus format for monitoring and alerting.
    """
    settings = get_settings()

    if not settings.metrics_enabled:
        return {"message": "Metrics collection is disabled"}

    # Generate Prometheus metrics
    metrics_data = generate_latest(REGISTRY)

    return Response(content=metrics_data, media_type=CONTENT_TYPE_LATEST)


@router.get("/metrics/json", response_model=MetricsResponse)
async def json_metrics() -> MetricsResponse:
    """
    JSON metrics endpoint for custom monitoring dashboards.

    Returns application metrics in JSON format.
    """
    enabled = get_settings().metrics_enabled
    measured = request_metrics.snapshot() if enabled else RequestMetricsValues()
    knowledge = (
        await collect_knowledge_counts()
        if enabled
        else KnowledgeCounts(status="disabled")
    )
    return MetricsResponse(
        enabled=enabled,
        metrics=measured,
        knowledge=knowledge,
        unavailable_metrics=[
            key for key, value in measured.model_dump().items() if value is None
        ],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
