from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config import settings
from app.api import customer_service_routing, executions, workflows
from app.integrations.http_client import HttpClient

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown
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

app.include_router(workflows.router, prefix=f"{settings.API_V1_STR}/workflows", tags=["workflows"])
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

