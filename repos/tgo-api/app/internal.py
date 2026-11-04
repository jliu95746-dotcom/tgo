"""Internal services FastAPI application.

This application runs on a separate port and is designed for inter-service
communication within the internal network. Route-level authentication varies.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import (
    TGOAPIException,
    general_exception_handler,
    http_exception_handler,
    tgo_api_exception_handler,
    validation_exception_handler,
)
from app.core.logging import setup_logging
from app.api.internal.router import internal_router

# Setup logging
setup_logging()

# Configure logging
logger = logging.getLogger("internal")
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)

# Create internal app
internal_app = FastAPI(
    title=f"{settings.PROJECT_NAME} - Internal Services",
    description="Internal services for inter-service communication; route-level authentication varies",
    version=settings.PROJECT_VERSION,
    openapi_url="/internal/openapi.json",
    docs_url="/internal/docs",
    redoc_url="/internal/redoc",
)

# Minimal CORS (only allow internal services)
internal_app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.INTERNAL_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)

# Add exception handlers
internal_app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
internal_app.add_exception_handler(Exception, http_exception_handler)
internal_app.add_exception_handler(RequestValidationError, validation_exception_handler)
internal_app.add_exception_handler(ValidationError, validation_exception_handler)
internal_app.add_exception_handler(Exception, general_exception_handler)

# Include internal router
internal_app.include_router(internal_router, prefix="/internal")


@internal_app.get("/")
async def internal_root() -> dict[str, str]:
    """Internal service root endpoint."""
    return {
        "message": "TGO-Tech Internal Services",
        "version": settings.PROJECT_VERSION,
        "note": "Internal use only; consult each route's authentication requirements.",
    }


@internal_app.get("/health")
async def internal_health_check() -> dict[str, str]:
    """Internal health check endpoint."""
    return {"status": "healthy", "service": "internal"}


@internal_app.on_event("startup")
async def internal_startup_event():
    """Internal application startup event."""
    from app.core.logging import startup_log

    startup_log("═" * 64)
    startup_log("🔧 TGO-Tech Internal Services Starting...")
    startup_log("═" * 64)
    startup_log(f"   📍 Listening on: {settings.INTERNAL_SERVICE_HOST}:{settings.INTERNAL_SERVICE_PORT}")
    startup_log(f"   📚 API Docs: http://localhost:{settings.INTERNAL_SERVICE_PORT}/internal/docs")
    startup_log(f"   🏥 Health Check: http://localhost:{settings.INTERNAL_SERVICE_PORT}/health")
    startup_log("")
    startup_log("⚠️  WARNING: Some internal routes have no authentication")
    startup_log("   Keep this service on a trusted internal network!")
    startup_log("")
    startup_log("🎉 Internal Services Ready!")
    startup_log("═" * 64)


@internal_app.on_event("shutdown")
async def internal_shutdown_event():
    """Internal application shutdown event."""
    logger.info("Internal services shutting down...")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.internal:internal_app",
        host=settings.INTERNAL_SERVICE_HOST,
        port=settings.INTERNAL_SERVICE_PORT,
        reload=True,
        log_level="info",
    )
