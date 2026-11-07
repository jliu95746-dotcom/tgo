"""Safe HTTP diagnostics without rendering request values or failures."""

from collections.abc import Awaitable, Callable
import logging
import time
from typing import TypedDict
import uuid

from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..logging_config import (
    LoggerAdapter,
    clear_request_context,
    get_logger,
    set_request_context,
)

logger = get_logger(__name__)


class ValidationIssue(TypedDict):
    type: str
    loc: list[str | int]
    msg: str


class RequestDiagnostics(TypedDict):
    method: str
    route: str


def request_diagnostics(request: Request) -> RequestDiagnostics:
    """Use declared routes and bounded methods, never an incoming URL."""
    route = getattr(request.scope.get("route"), "path", None)
    method = request.method
    return {
        "method": method
        if method
        in {
            "GET",
            "HEAD",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
            "TRACE",
            "CONNECT",
        }
        else "OTHER",
        "route": route if isinstance(route, str) else "unmatched",
    }


def public_validation_errors(
    error: RequestValidationError,
) -> list[ValidationIssue]:
    """Keep field locations and codes without values or validator messages."""
    messages = {
        "missing": "Field required",
        "int_type": "Input should be a valid integer",
        "int_parsing": "Input should be a valid integer",
        "string_type": "Input should be a valid string",
        "json_invalid": "Invalid JSON",
    }
    issues: list[ValidationIssue] = []
    for item in error.errors():
        kind = item.get("type")
        code = kind if isinstance(kind, str) else "validation_error"
        location = item.get("loc", ())
        issues.append(
            {
                "type": code,
                "loc": [
                    part for part in location if isinstance(part, (str, int))
                ],
                "msg": messages.get(code, "Invalid value"),
            }
        )
    return issues


class RequestLoggingMiddleware:
    """Track requests without adapting or buffering their response bodies."""

    def __init__(self, app: ASGIApp, logger: LoggerAdapter) -> None:
        self.app = app
        self.logger = logger

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        started_at = time.time()
        request_id = str(uuid.uuid4())
        set_request_context(request_id=request_id)

        async def observe(message: Message) -> None:
            if message["type"] == "http.response.start":
                elapsed = time.time() - started_at
                self.logger.info(
                    "Request completed",
                    status_code=message["status"],
                    process_time_ms=round(elapsed * 1000, 2),
                    **request_diagnostics(request),
                )
                headers = MutableHeaders(scope=message)
                headers["X-Process-Time"] = str(elapsed)
                headers["X-Request-ID"] = request_id
            await send(message)

        try:
            self.logger.info("Request started", **request_diagnostics(request))
            await self.app(scope, receive, observe)
        finally:
            clear_request_context()


ErrorHandler = Callable[[Request, Exception], Awaitable[Response]]


class HTTPErrorPrivacyMiddleware:
    """Stop private failures escaping into the server's error formatter."""

    def __init__(self, app: ASGIApp, handler: ErrorHandler) -> None:
        self.app = app
        self.handler = handler
        # Use safe application diagnostics instead of the server access log,
        # which includes raw URLs and client addresses.
        logging.getLogger("uvicorn.access").disabled = True

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def observe(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, observe)
        except Exception as error:
            request = Request(scope)
            if started:
                logger.error(
                    "Response body failed",
                    error_type=type(error).__name__,
                    **request_diagnostics(request),
                )
                # Keep the failed/incomplete response visible to the server,
                # without a second response or the private exception chain.
                raise RuntimeError("Response body failed") from None
            response = await self.handler(request, error)
            await response(scope, receive, send)
