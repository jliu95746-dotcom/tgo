"""Internal services router.

These endpoints are designed for inter-service communication within the
internal network. Each route declares its own authentication requirements.
"""

from fastapi import APIRouter

from app.api.internal.endpoints import ai_events, ai_providers, users, store
from app.api.internal.endpoints import reply_phases
from app.api.internal.endpoints import ai_usage
from app.api.internal.endpoints import wecom_native
from app.api.internal.endpoints import agent_activation
from app.api.internal.endpoints import business_connections
from app.api.internal.endpoints import agent_business_trials
from app.api.internal.endpoints import business_execution_context
from app.api.internal.endpoints import agent_business_execution
from app.api.internal.endpoints import channel_visitors
from app.api.internal.endpoints import workflow_approvals
from app.api.internal.endpoints import workflow_event_sources
from app.api.internal.endpoints import workflow_schedules
from app.api.internal.endpoints import workflow_webhooks
from app.api.v1.endpoints.wukongim_webhook import handle_wukongim_webhook

internal_router = APIRouter()
internal_router.include_router(workflow_webhooks.router, prefix="/workflows/webhooks", tags=["Private webhook authority"])
internal_router.include_router(workflow_schedules.router, prefix="/workflows/schedules", tags=["Private timer authority"])
internal_router.include_router(workflow_event_sources.router, prefix="/workflows/events", tags=["Private workflow event sources"])
internal_router.include_router(wecom_native.router, prefix="/wecom-native", tags=["Private WeCom native service"])
internal_router.include_router(channel_visitors.router, prefix="/channel-visitors", tags=["Private channel visitors"])
internal_router.include_router(workflow_approvals.router, prefix="/workflows/approvals", tags=["Private workflow approvals"])
internal_router.include_router(agent_business_execution.router, tags=["Private business execution"])
internal_router.include_router(business_execution_context.router, tags=["Private business execution context"])
internal_router.include_router(agent_business_trials.router, tags=["Private employee business tests"])
internal_router.include_router(business_connections.router, prefix="/business-connections", tags=["Private business connections"])
internal_router.include_router(ai_usage.router, prefix="/billing/usage", tags=["Private quota"])
internal_router.include_router(
    agent_activation.router, prefix="/ai/activation", tags=["Private agent activation"],
)

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

# AI event mutations require the shared internal service key.
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
