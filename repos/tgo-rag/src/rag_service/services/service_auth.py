"""Accept RAG requests only from trusted gateways when SaaS is enabled."""

from secrets import compare_digest

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import get_settings


class ServiceIdentityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            settings = get_settings()
            if (
                settings.saas_enabled
                and settings.saas_billing_enabled
                and request.url.path.startswith("/v1/")
            ):
                provided = request.headers.get("X-SaaS-Service-Token", "")
                if (
                    not settings.saas_internal_token
                    or not provided
                    or not compare_digest(
                        provided,
                        settings.saas_internal_token.get_secret_value(),
                    )
                ):
                    response = JSONResponse(
                        status_code=403,
                        content={"detail": "内部服务身份无效"},
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
