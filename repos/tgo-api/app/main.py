"""FastAPI application entry point with extension support."""

from typing import Callable, List, Optional, Any
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter
from pydantic import ValidationError

from app.api.v1.router import api_router
from app.api.openapi import TGOApplication
from app.core.config import settings
from app.core.dev_data import log_startup_banner, ensure_permissions_seed
from app.core.exceptions import (
    TGOAPIException,
    general_exception_handler,
    http_exception_handler,
    tgo_api_exception_handler,
    validation_exception_handler,
)
from app.core.logging import setup_logging
from app.services.platform_type_seed import ensure_platform_types_seed


# Setup logging
setup_logging()

# Configure uvicorn logging for cleaner startup
import logging

logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)


def create_app(
    additional_routers: Optional[List[APIRouter]] = None,
    additional_middlewares: Optional[List[tuple[Any, dict]]] = None,
    startup_hooks: Optional[List[Callable]] = None,
    shutdown_hooks: Optional[List[Callable]] = None,
) -> FastAPI:
    (
        '\n'
        '    Create and configure the FastAPI application instance.\n'
        '    \n'
        '    This factory function allows extensions (like SaaS modules) to '
        'inject\n'
        '    additional routers, middlewares, and lifecycle hooks.\n'
        '    \n'
        '    Args:\n'
        '        additional_routers: List of APIRouter instances to include\n'
        '        additional_middlewares: List of (middleware_class, kwargs) '
        'tuples\n'
        '        startup_hooks: List of async functions to run on startup\n'
        '        shutdown_hooks: List of async functions to run on shutdown\n'
        '    \n'
        '    Returns:\n'
        '        Configured FastAPI application instance\n'
        '    \n'
        '    Example:\n'
        '        # In SaaS extension:\n'
        '        from app.main import create_app\n'
        '        from .routers import billing_router\n'
        '        from .middlewares import UsageMiddleware\n'
        '        \n'
        '        app = create_app(\n'
        '            additional_routers=[billing_router],\n'
        '            additional_middlewares=[(UsageMiddleware, {})],\n'
        '        )\n'
        '    '
    )
    application = TGOApplication(
        title=settings.PROJECT_NAME,
        description=settings.PROJECT_DESCRIPTION,
        version=settings.PROJECT_VERSION,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        docs_url=f"{settings.API_V1_STR}/docs",
        redoc_url=f"{settings.API_V1_STR}/redoc",
    )

    # Set up CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Session-Operation-ID",
            "X-Session-Notification-Status",
        ],
    )

    # Add additional middlewares (before exception handlers)
    if additional_middlewares:
        for middleware_class, kwargs in additional_middlewares:
            application.add_middleware(middleware_class, **kwargs)

    # Add exception handlers
    application.add_exception_handler(
        TGOAPIException, tgo_api_exception_handler
    )
    application.add_exception_handler(HTTPException, http_exception_handler)
    application.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )
    application.add_exception_handler(
        ValidationError, validation_exception_handler
    )
    application.add_exception_handler(Exception, general_exception_handler)

    # Include core API router
    application.include_router(api_router, prefix=settings.API_V1_STR)

    # Include additional routers
    if additional_routers:
        for router in additional_routers:
            application.include_router(router, prefix=settings.API_V1_STR)

    # Register startup event
    @application.on_event("startup")
    async def startup_event() -> None:
        """Application startup event."""
        from app.core.logging import startup_log

        # Display startup banner
        log_startup_banner()

        # Database connection
        # Ensure platform types seeded (idempotent)
        try:
            ensure_platform_types_seed()
        except Exception:
            # best-effort; don't block startup
            pass

        # Ensure permission definitions seeded (idempotent)
        try:
            ensure_permissions_seed()
        except Exception:
            # best-effort; don't block startup
            pass

        startup_log("🗄️  Connecting to database...")

        from app.tasks.recover_staff_delivery_history import (
            start_staff_history_recovery,
        )

        start_staff_history_recovery()
        from app.tasks.workflow_source_dispatch import (
            start_workflow_source_dispatch,
        )

        start_workflow_source_dispatch()
        from app.tasks.company_mail import start_company_mail

        start_company_mail()
        from app.tasks.billing import start_billing

        start_billing()
        from app.tasks.finalize_shadow_protocols import (
            start_shadow_protocol_finalization,
        )

        start_shadow_protocol_finalization()
        from app.tasks.assist_draft_retention import (
            start_assist_draft_retention,
        )

        start_assist_draft_retention()
        from app.tasks.visitor_avatar_retention import (
            start_visitor_avatar_retention,
        )

        start_visitor_avatar_retention()
        from app.tasks.visitor_end_recovery import start_visitor_end_recovery

        start_visitor_end_recovery()
        from app.services.customer_turn_worker import start

        start()

        # Start background sync monitor
        try:
            from app.services.platform_sync import start_sync_monitor

            start_sync_monitor()
        except Exception:
            # best-effort; don't block startup
            pass

        # Start periodic AIProvider sync task (best-effort)
        try:
            from app.tasks.sync_ai_providers import start_ai_provider_sync_task

            start_ai_provider_sync_task()
        except Exception:
            # best-effort; don't block startup
            pass

        # Start periodic ProjectAIConfig sync task (best-effort)
        try:
            from app.tasks.sync_project_ai_configs import (
                start_project_ai_config_sync_task,
            )

            start_project_ai_config_sync_task()
        except Exception:
            # best-effort; don't block startup
            pass

        # Start periodic waiting queue processor task (best-effort)
        try:
            from app.tasks.process_waiting_queue import start_queue_processor

            start_queue_processor()
        except Exception:
            # best-effort; don't block startup
            pass

        # Start periodic session timeout check task (best-effort)
        try:
            from app.tasks.close_timeout_sessions import (
                start_session_timeout_task,
            )

            await start_session_timeout_task()
        except Exception:
            # best-effort; don't block startup
            pass

        # Start periodic visitor online status sync task (best-effort)
        try:
            from app.tasks.sync_visitor_online_status import (
                start_visitor_online_sync_task,
            )

            await start_visitor_online_sync_task()
        except Exception:
            # best-effort; don't block startup
            pass

        # Start periodic auto AI fallback task (best-effort)
        try:
            from app.tasks.auto_fallback_to_ai import (
                start_auto_fallback_to_ai_task,
            )

            await start_auto_fallback_to_ai_task()
        except Exception:
            # best-effort; don't block startup
            pass

        # Run additional startup hooks
        if startup_hooks:
            for hook in startup_hooks:
                try:
                    result = hook()
                    if hasattr(result, '__await__'):
                        await result
                except Exception:
                    # best-effort; don't block startup
                    pass

        # Server ready
        startup_log("🌐 Server starting...")
        startup_log(f"   📍 Listening on: http://0.0.0.0:8000")
        startup_log(f"   📚 API Docs: http://localhost:8000/v1/docs")
        startup_log(f"   🏥 Health Check: http://localhost:8000/health")
        startup_log("")
        startup_log("🎉 TGO-Tech API Service is ready!")
        startup_log("═" * 64)

    # Register shutdown event
    @application.on_event("shutdown")
    async def shutdown_event() -> None:
        from app.services.customer_turn_worker import stop

        await stop()
        """Application shutdown event: stop background tasks."""
        from app.tasks.recover_staff_delivery_history import (
            stop_staff_history_recovery,
        )

        await stop_staff_history_recovery()
        from app.tasks.company_mail import stop_company_mail

        await stop_company_mail()
        from app.tasks.billing import stop_billing

        await stop_billing()
        from app.tasks.finalize_shadow_protocols import (
            stop_shadow_protocol_finalization,
        )

        await stop_shadow_protocol_finalization()
        from app.tasks.assist_draft_retention import (
            stop_assist_draft_retention,
        )

        await stop_assist_draft_retention()
        from app.tasks.visitor_avatar_retention import (
            stop_visitor_avatar_retention,
        )

        await stop_visitor_avatar_retention()
        from app.tasks.visitor_end_recovery import stop_visitor_end_recovery

        await stop_visitor_end_recovery()
        from app.tasks.workflow_source_dispatch import (
            stop_workflow_source_dispatch,
        )

        await stop_workflow_source_dispatch()
        # Stop periodic AIProvider sync task (best-effort)
        try:
            from app.tasks.sync_ai_providers import stop_ai_provider_sync_task

            await stop_ai_provider_sync_task()
        except Exception:
            pass

        # Stop periodic ProjectAIConfig sync task (best-effort)
        try:
            from app.tasks.sync_project_ai_configs import (
                stop_project_ai_config_sync_task,
            )

            await stop_project_ai_config_sync_task()
        except Exception:
            pass

        # Stop periodic waiting queue processor task (best-effort)
        try:
            from app.tasks.process_waiting_queue import stop_queue_processor

            await stop_queue_processor()
        except Exception:
            pass

        # Stop periodic session timeout check task (best-effort)
        try:
            from app.tasks.close_timeout_sessions import (
                stop_session_timeout_task,
            )

            await stop_session_timeout_task()
        except Exception:
            pass

        # Stop periodic visitor online status sync task (best-effort)
        try:
            from app.tasks.sync_visitor_online_status import (
                stop_visitor_online_sync_task,
            )

            await stop_visitor_online_sync_task()
        except Exception:
            pass

        # Stop periodic auto AI fallback task (best-effort)
        try:
            from app.tasks.auto_fallback_to_ai import (
                stop_auto_fallback_to_ai_task,
            )

            await stop_auto_fallback_to_ai_task()
        except Exception:
            pass

        # Run additional shutdown hooks
        if shutdown_hooks:
            for hook in shutdown_hooks:
                try:
                    result = hook()
                    if hasattr(result, '__await__'):
                        await result
                except Exception:
                    pass

    # Register root endpoints
    @application.get("/")
    async def root() -> dict[str, str]:
        """Root endpoint."""
        return {
            "message": "TGO-Tech API Service",
            "version": settings.PROJECT_VERSION,
        }

    @application.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy"}

    return application


# Create default application instance (for backward compatibility)
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
