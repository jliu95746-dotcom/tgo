from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from contextlib import suppress
import asyncio
from app.config import settings
from app.api import (
    approvals, customer_service_routing, events, executions, schedules, workflows,
    webhooks,
    webhook_deliveries,
)
from app.integrations.http_client import HttpClient
from app.api import ai_runtime
from app.services.workflow_ai_result_retention import reap_forever

@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup = asyncio.create_task(reap_forever())
    try:
        yield
    finally:
        cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup
        await HttpClient.close_client()

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Set all CORS enabled origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(approvals.router, prefix=f"{settings.API_V1_STR}/workflows", tags=["approvals"])
app.include_router(
    ai_runtime.router, prefix=f"{settings.API_V1_STR}/workflows",
    tags=["private-ai-runtime"],
)
app.include_router(workflows.router, prefix=f"{settings.API_V1_STR}/workflows", tags=["workflows"])
app.include_router(
    schedules.router, prefix=f"{settings.API_V1_STR}/workflows", tags=["schedules"],
)
app.include_router(events.router, prefix=f"{settings.API_V1_STR}/workflows", tags=["events"])
app.include_router(
    webhooks.router, prefix=f"{settings.API_V1_STR}/workflows", tags=["webhooks"],
)
app.include_router(webhook_deliveries.router,
                   prefix=f"{settings.API_V1_STR}/webhook-deliveries",
                   tags=["Private webhook ingress"])
app.include_router(executions.router, prefix=f"{settings.API_V1_STR}/workflows", tags=["executions"])
app.include_router(
    customer_service_routing.router,
    prefix=f"{settings.API_V1_STR}/customer-service",
    tags=["customer-service-routing"],
)

from app.schemas.common import MessageResponse, HealthCheckResponse

@app.get("/", response_model=MessageResponse)
async def root():
    return MessageResponse(message="TGO Workflow API is running")

@app.get("/health", response_model=HealthCheckResponse)
async def health_check():
    return HealthCheckResponse(status="healthy")

