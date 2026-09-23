"""Internal services router.

This router includes all internal endpoints that do not require authentication.
These endpoints are designed for inter-service communication within the internal network.
"""

from fastapi import APIRouter

from app.api.internal.endpoints import ai_events, ai_providers, users, store
from app.api.internal.endpoints import reply_phases
from app.api.internal.endpoints import ai_usage
from app.api.v1.endpoints.wukongim_webhook import handle_wukongim_webhook

internal_router = APIRouter()
internal_router.include_router(ai_usage.router, prefix="/billing/usage", tags=["Private quota"])

internal_router.add_api_route(
    "/integrations/wukongim/webhook",
    handle_wukongim_webhook,
    methods=["POST"],
    tags=["Internal IM Events"],
)

internal_router.include_router(
    reply_phases.router,
    prefix="/ai/reply-phases",
    tags=["Internal Reply Control"],
)

# Include internal endpoints (no authentication required)
internal_router.include_router(
    ai_events.router,
    prefix="/ai/events",
    tags=["Internal AI Events"]
)

# AI Providers endpoint (for tgo-vision-agent to fetch provider config)
internal_router.include_router(
    ai_providers.router,
    prefix="/ai-providers",
    tags=["Internal AI Providers"]
)

# New users endpoint
internal_router.include_router(
    users.router,
    prefix="/users",
    tags=["Internal Users"]
)

# Store endpoint
internal_router.include_router(
    store.router,
    prefix="/store",
    tags=["Internal Store"]
)
