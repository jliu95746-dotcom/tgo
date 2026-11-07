"""Unified documentation access for multiple microservices.

Provides access to OpenAPI/Swagger documentation for:
- /docs/api - Current API service (tgo-api)
- /docs/ai - AI service
- /docs/rag - RAG service
- /docs/platform - Platform service
"""

from html import escape
from typing import TypedDict

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("docs")

router = APIRouter(prefix="/docs", tags=["Documentation"])


class ServiceDefinition(TypedDict):
    name: str
    url: str | None
    openapi_path: str


# Service configuration mapping
SERVICES: dict[str, ServiceDefinition] = {
    "api": {
        "name": "TGO API Service",
        "url": None,  # Local service, use internal openapi
        "openapi_path": f"{settings.API_V1_STR}/openapi.json",
    },
    "ai": {
        "name": "AI Service",
        "url": settings.AI_SERVICE_URL,
        "openapi_path": "/openapi.json",
    },
    "rag": {
        "name": "RAG Service",
        "url": settings.RAG_SERVICE_URL,
        "openapi_path": "/openapi.json",
    },
    "platform": {
        "name": "Platform Service",
        "url": settings.PLATFORM_SERVICE_URL,
        "openapi_path": "/openapi.json",
    },
    "workflow": {
        "name": "Workflow Service",
        "url": settings.WORKFLOW_SERVICE_URL,
        "openapi_path": "/v1/openapi.json",
    },
}


def _swagger_ui_html(title: str, openapi_url: str) -> str:
    """Generate Swagger UI HTML page."""
    return (
        f'\n'
        f'<!DOCTYPE html>\n'
        f'<html>\n'
        f'<head>\n'
        f'    <title>'
        f'{title}'
        f' - Swagger UI</title>\n'
        f'    <meta charset="utf-8"/>\n'
        f'    <meta name="viewport" content="width=device-width, '
        f'initial-scale=1">\n'
        f'    <link rel="stylesheet" type="text/css" '
        f'href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">\n'
        f'</head>\n'
        f'<body>\n'
        f'    <div id="swagger-ui"></div>\n'
        f'    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bund'
        f'le.js"></script>\n'
        f'    <script>\n'
        f'        SwaggerUIBundle({{\n'
        f'            url: "'
        f'{openapi_url}'
        f'",\n'
        f"            dom_id: '#swagger-ui',\n"
        f'            presets: [SwaggerUIBundle.presets.apis, '
        f'SwaggerUIBundle.SwaggerUIStandalonePreset],\n'
        f'            layout: "StandaloneLayout"\n'
        f'        }});\n'
        f'    </script>\n'
        f'</body>\n'
        f'</html>\n'
    )


def _error_html(title: str, message: str) -> str:
    """Generate error page HTML."""
    title = escape(title)
    message = escape(message)
    return (
        f'\n'
        f'<!DOCTYPE html>\n'
        f'<html>\n'
        f'<head>\n'
        f'    <title>'
        f'{title}'
        f' - Error</title>\n'
        f'    <style>\n'
        f'        body {{ font-family: Arial, sans-serif; padding: 40px; '
        f'background: #f5f5f5; }}\n'
        f'        .error {{ background: white; padding: 30px; border-radius: '
        f'8px; max-width: 600px; margin: 0 auto; box-shadow: 0 2px 10px '
        f'rgba(0,0,0,0.1); }}\n'
        f'        h1 {{ color: #e74c3c; }}\n'
        f'        p {{ color: #666; }}\n'
        f'    </style>\n'
        f'</head>\n'
        f'<body>\n'
        f'    <div class="error">\n'
        f'        <h1>⚠️ '
        f'{title}'
        f'</h1>\n'
        f'        <p>'
        f'{message}'
        f'</p>\n'
        f'    </div>\n'
        f'</body>\n'
        f'</html>\n'
    )


@router.get("", response_class=HTMLResponse, summary="Documentation Index")
async def docs_index() -> HTMLResponse:
    """List all available service documentation."""
    links = "".join(
        f'<li><a href="/v1/docs/{key}">{svc["name"]}</a></li>'
        for key, svc in SERVICES.items()
    )
    return HTMLResponse(
        (
            f'\n'
            f'<!DOCTYPE html>\n'
            f'<html>\n'
            f'<head><title>API Documentation</title>\n'
            f'<style>\n'
            f'    body {{ font-family: Arial, sans-serif; padding: 40px; '
            f'background: #f5f5f5; }}\n'
            f'    .container {{ background: white; padding: 30px; '
            f'border-radius: 8px; max-width: 600px; margin: 0 auto; }}\n'
            f'    h1 {{ color: #333; }} ul {{ line-height: 2; }} a {{ color: '
            f'#3498db; }}\n'
            f'</style>\n'
            f'</head>\n'
            f'<body><div class="container"><h1>📚 API Documentation</h1><ul>'
            f'{links}'
            f'</ul></div></body>\n'
            f'</html>\n'
        )
    )


@router.get(
    "/api", response_class=HTMLResponse, summary="TGO API Documentation"
)
async def docs_api() -> HTMLResponse:
    """Swagger UI for the current TGO API service."""
    return HTMLResponse(
        _swagger_ui_html(
            "TGO API Service", f"{settings.API_V1_STR}/openapi.json"
        )
    )


@router.get("/{service}/openapi.json", summary="Proxy OpenAPI JSON")
async def proxy_openapi_json(service: str) -> JSONResponse:
    """Proxy OpenAPI JSON from remote service."""
    if service not in SERVICES or service == "api":
        raise HTTPException(status_code=404, detail="Service not found")
    svc = SERVICES[service]
    service_url = svc['url']
    if not service_url:
        raise HTTPException(
            status_code=503, detail=f"Service unavailable: {svc['name']}"
        )
    url = f"{service_url.rstrip('/')}{svc['openapi_path']}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return JSONResponse(content=resp.json())
    except Exception as exc:
        logger.warning(
            "Failed to fetch service OpenAPI",
            extra={"service": service, "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=503, detail=f"Service unavailable: {svc['name']}"
        ) from None


@router.get(
    "/{service}", response_class=HTMLResponse, summary="Service Documentation"
)
async def docs_service(service: str) -> HTMLResponse:
    """Swagger UI for a remote microservice."""
    if service not in SERVICES:
        return HTMLResponse(
            _error_html("Not Found", f"Service '{service}' not found."),
            status_code=404,
        )
    if service == "api":
        return HTMLResponse(
            _swagger_ui_html(
                "TGO API Service", f"{settings.API_V1_STR}/openapi.json"
            )
        )
    svc = SERVICES[service]
    # Use proxied openapi.json to avoid CORS issues

    openapi_url = f"/v1/docs/{service}/openapi.json"
    return HTMLResponse(_swagger_ui_html(svc["name"], openapi_url))
