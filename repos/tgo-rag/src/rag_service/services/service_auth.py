"""Accept RAG requests only from trusted gateways when SaaS is enabled."""

from secrets import compare_digest

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import get_settings


class ServiceIdentityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if settings.saas_enabled and settings.saas_billing_enabled and request.url.path.startswith('/v1/'):
            provided = request.headers.get('X-SaaS-Service-Token', '')
            if not settings.saas_internal_token or not provided or not compare_digest(provided, settings.saas_internal_token.get_secret_value()):
                return JSONResponse(status_code=403, content={"detail": "内部服务身份无效"})
        return await call_next(request)
