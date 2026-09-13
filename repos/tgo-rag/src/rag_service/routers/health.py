"""
Health check endpoints.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response

from ..config import get_settings
from ..services.health_checks import collect_health_checks
from ..schemas.common import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(response: Response) -> HealthResponse:
    """
    Comprehensive health check endpoint.

    Returns the overall health status of the service and its dependencies.
    """
    checks = await collect_health_checks()
    if not checks.healthy:
        response.status_code = 503
    return HealthResponse(
        status="healthy" if checks.healthy else "unhealthy",
        version=get_settings().app_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        checks=checks,
    )


@router.get("/ready")
async def readiness_check() -> dict[str, str]:
    """
    Kubernetes readiness probe endpoint.

    Returns 200 if the service is ready to accept traffic, 503 otherwise.
    """
    checks = await collect_health_checks()
    if not checks.healthy:
        raise HTTPException(status_code=503, detail="Service not ready")
    return {"status": "ready"}


@router.get("/live")
async def liveness_check() -> dict[str, str]:
    """
    Kubernetes liveness probe endpoint.

    Returns 200 if the service is alive, 503 if it should be restarted.
    """
    # Simple liveness check - just return OK
    # In a real implementation, you might check for deadlocks, memory leaks, etc.
    return {"status": "alive"}
