"""HTTP boundary for deterministic customer-service routing."""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.customer_service_routing import (
    CustomerServiceRoutingDecision,
    CustomerServiceRoutingRequest,
)
from app.services.customer_service_routing_service import (
    CustomerServiceRoutingService,
)


router = APIRouter()


@router.post(
    "/route",
    response_model=CustomerServiceRoutingDecision,
    summary="Select a safe customer-service route",
)
async def route_customer_service_message(
    request: CustomerServiceRoutingRequest,
) -> CustomerServiceRoutingDecision:
    """Apply the side-effect-free policy to trusted classification metadata."""
    return CustomerServiceRoutingService.route(request.to_domain())
