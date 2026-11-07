"""
Main FastAPI application for TGO RAG Service.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, get_settings
from .database import close_database, init_database
from .logging_config import (
    get_logger,
    init_logging_from_settings,
)
from .routers import (
    collections,
    embedding_config,
    files,
    health,
    knowledge_governance,
    knowledge_versions,
    monitoring,
    qa,
    websites,
)
from .schemas.common import ErrorResponse
from .services.http_diagnostics import (
    HTTPErrorPrivacyMiddleware,
    RequestLoggingMiddleware,
    public_validation_errors,
    request_diagnostics,
)
from .services.request_metrics import RequestMetricsMiddleware
from .services.service_auth import ServiceIdentityMiddleware
from .startup_banner import (
    print_startup_banner,
    print_config_info,
    print_startup_summary,
    log_startup_step,
    log_startup_success,
    log_startup_error,
    print_section_header,
    print_section_footer,
    Symbols,
)

# Initialize centralized logging configuration
init_logging_from_settings()

# Get logger for this module
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage application startup and shutdown with enhanced logging.
    """
    settings = get_settings()
    dev_project_created = False

    # Print beautiful startup banner
    print_startup_banner(
        settings.app_name, settings.app_version, settings.environment
    )

    # Show configuration information
    config_info = {
        "Environment": settings.environment,
        "Debug Mode": settings.debug,
        "Host": settings.host,
        "Port": settings.port,
        "Database URL": settings.database_url,
        "Redis URL": settings.redis_url,
        "Log Level": settings.log_level,
    }
    print_config_info(config_info)

    # Startup process
    print_section_header("Initialization", Symbols.GEAR)
    log_startup_step(
        "Starting TGO RAG Service initialization",
        version=settings.app_version,
        environment=settings.environment,
    )

    try:
        # Initialize database
        log_startup_step("Initializing database connection...")
        await init_database()
        log_startup_success("Database initialized successfully")

        # TODO: Initialize other services (Redis, Vector DB, etc.)
        log_startup_step("Initializing additional services...")
        log_startup_success("Additional services initialized")

        print_section_footer()

        # Print startup summary
        print_startup_summary(
            host=settings.host,
            port=settings.port,
            environment=settings.environment,
            docs_enabled=settings.debug,
            dev_project_created=dev_project_created,
        )

        yield

    except Exception as e:
        log_startup_error("Failed to start application", error=str(e))
        print_section_footer()
        raise
    finally:
        # Shutdown
        print_section_header("Shutdown", Symbols.GEAR)
        log_startup_step("Shutting down TGO RAG Service")
        await close_database()
        log_startup_success("Application shutdown complete")
        print_section_footer()


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        FastAPI: Configured application instance
    """
    settings = get_settings()

    # Create FastAPI app with custom configuration and OpenAPI security scheme
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "TGO-Tech RAG (Retrieval-Augmented Generation) Service "
            "for document processing and semantic search"
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # API Key security scheme removed (internal-only service)

    # Add middleware
    setup_middleware(app, settings)

    # Add routers
    setup_routers(app)

    # Add exception handlers
    setup_exception_handlers(app)

    # Root endpoint
    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        """Root endpoint with basic service information."""
        settings = get_settings()
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "status": "running",
            "docs_url": "/docs",
            "health_check": "/health",
        }

    return app


def setup_middleware(app: FastAPI, settings: Settings) -> None:
    """
    Configure application middleware.

    Args:
        app: FastAPI application instance
        settings: Application settings
    """
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )

    # Trusted host middleware (security)
    if settings.environment == "production":
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

    app.add_middleware(RequestLoggingMiddleware, logger=logger)

    # Observe middleware replies (including CORS) as well as route replies.
    app.add_middleware(
        RequestMetricsMiddleware, enabled=settings.metrics_enabled
    )
    app.add_middleware(ServiceIdentityMiddleware)


def setup_routers(app: FastAPI) -> None:
    """
    Configure application routers.

    Args:
        app: FastAPI application instance
    """
    # Health and monitoring endpoints
    app.include_router(health.router, tags=["Health"])
    app.include_router(monitoring.router, tags=["Monitoring"])

    # API v1 endpoints
    api_v1_prefix = "/v1"

    # Collections endpoints
    app.include_router(
        collections.router,
        prefix=f"{api_v1_prefix}/collections",
        tags=["Collections"],
    )

    # Files endpoints
    app.include_router(
        files.router, prefix=f"{api_v1_prefix}/files", tags=["Files"]
    )

    app.include_router(
        knowledge_governance.router,
        prefix=f"{api_v1_prefix}/knowledge-governance",
        tags=["Knowledge Governance"],
    )
    # Embedding configuration endpoints (no auth)
    app.include_router(
        knowledge_versions.router,
        prefix=f"{api_v1_prefix}/knowledge-versions",
        tags=["Knowledge Versions"],
    )
    app.include_router(
        embedding_config.router,
        prefix=f"{api_v1_prefix}/embedding-configs",
        tags=["Embedding Configs"],
    )

    # Website crawling endpoints
    app.include_router(
        websites.router,
        prefix=f"{api_v1_prefix}/websites",
        tags=["Websites"],
    )

    # QA knowledge base endpoints
    app.include_router(
        qa.router,
        prefix=api_v1_prefix,
        tags=["QA Knowledge Base"],
    )

    # TODO: Add more routers (documents, search, etc.)


def setup_exception_handlers(app: FastAPI) -> None:
    """
    Configure global handlers with a consistent error response format.

    Args:
        app: FastAPI application instance
    """

    # Use one HTTP error handler for Starlette and FastAPI exceptions.
    @app.exception_handler(StarletteHTTPException)
    @app.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        """Handle HTTP exceptions with the common ErrorResponse format."""
        error_code_map = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            413: "PAYLOAD_TOO_LARGE",
            415: "UNSUPPORTED_MEDIA_TYPE",
            422: "UNPROCESSABLE_ENTITY",
            429: "TOO_MANY_REQUESTS",
            500: "INTERNAL_SERVER_ERROR",
            502: "BAD_GATEWAY",
            503: "SERVICE_UNAVAILABLE",
            504: "GATEWAY_TIMEOUT",
        }

        status_code = exc.status_code
        detail = exc.detail
        error_code = error_code_map.get(status_code, "HTTP_ERROR")

        logger.warning(
            "HTTP exception occurred",
            status_code=status_code,
            **request_diagnostics(request),
        )

        return JSONResponse(
            status_code=status_code,
            content=ErrorResponse(
                error={
                    "code": error_code,
                    "message": detail,
                    "details": {"status_code": status_code},
                },
                request_id=None,
            ).model_dump(),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Handle Pydantic errors with the common ErrorResponse format."""
        errors = public_validation_errors(exc)
        logger.warning(
            "Validation error occurred",
            error_count=len(errors),
            **request_diagnostics(request),
        )

        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error={
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": {"validation_errors": errors},
                },
                request_id=None,
            ).model_dump(),
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(
        request: Request,
        exc: ValueError,
    ) -> JSONResponse:
        """Handle ValueError exceptions."""
        logger.error(
            "ValueError occurred",
            error_type=type(exc).__name__,
            **request_diagnostics(request),
        )
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error={
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid request value",
                    "details": {"type": "ValueError"},
                },
                request_id=None,
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """Handle all other exceptions."""
        logger.error(
            "Unhandled exception occurred",
            error_type=type(exc).__name__,
            **request_diagnostics(request),
        )
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error={
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An internal server error occurred",
                    "details": {"type": type(exc).__name__},
                },
                request_id=None,
            ).model_dump(),
        )

    app.add_middleware(
        HTTPErrorPrivacyMiddleware,
        handler=general_exception_handler,
    )


# Create the application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "src.rag_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
        # Retain application logging when disabling Uvicorn's log config.
        # log_config=None,  # Uncomment this if warning logs don't appear
    )
