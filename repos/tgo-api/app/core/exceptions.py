"""Custom exceptions and error handling."""

from collections.abc import Mapping
from typing import Optional, TypedDict, get_args
from uuid import uuid4

from fastapi import HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import (
    http_exception_handler as default_http_exception_handler,
)
from fastapi.routing import APIRoute
from pydantic import BaseModel
from pydantic_core.core_schema import ErrorType
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.routing import Route
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import bind_error_request_id, get_logger
from app.schemas.base import ErrorDetail, ErrorResponse
from pydantic import ValidationError as PydanticValidationError

logger = get_logger("exceptions")


class TGOAPIException(Exception):
    """Base exception for TGO API."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        details: Optional[Mapping[str, object]] = None,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    ) -> None:
        self.message = message
        self.code = code
        self.details = dict(details or {})
        self.status_code = status_code
        super().__init__(self.message)


class ValidationError(TGOAPIException):
    """Validation error exception."""

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        details: Optional[Mapping[str, object]] = None,
    ) -> None:
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            details={"field": field, **(details or {})},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


class NotFoundError(TGOAPIException):
    """Resource not found exception."""

    def __init__(
        self,
        resource: str,
        identifier: Optional[str] = None,
        details: Optional[Mapping[str, object]] = None,
    ) -> None:
        message = f"{resource} not found"
        if identifier:
            message += f" with identifier: {identifier}"

        super().__init__(
            message=message,
            code="NOT_FOUND",
            details={
                "resource": resource,
                "identifier": identifier,
                **(details or {}),
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )


class ConflictError(TGOAPIException):
    """Resource conflict exception."""

    def __init__(
        self,
        message: str,
        resource: Optional[str] = None,
        details: Optional[Mapping[str, object]] = None,
    ) -> None:
        super().__init__(
            message=message,
            code="CONFLICT",
            details={"resource": resource, **(details or {})},
            status_code=status.HTTP_409_CONFLICT,
        )


class AuthenticationError(TGOAPIException):
    """Authentication error exception."""

    def __init__(
        self,
        message: str = "Authentication failed",
        details: Optional[Mapping[str, object]] = None,
    ) -> None:
        super().__init__(
            message=message,
            code="AUTHENTICATION_ERROR",
            details=details,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class AuthorizationError(TGOAPIException):
    """Authorization error exception."""

    def __init__(
        self,
        message: str = "Access denied",
        resource: Optional[str] = None,
        details: Optional[Mapping[str, object]] = None,
    ) -> None:
        super().__init__(
            message=message,
            code="AUTHORIZATION_ERROR",
            details={"resource": resource, **(details or {})},
            status_code=status.HTTP_403_FORBIDDEN,
        )


class RateLimitError(TGOAPIException):
    """Rate limit exceeded exception."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: Optional[int] = None,
        details: Optional[Mapping[str, object]] = None,
    ) -> None:
        super().__init__(
            message=message,
            code="RATE_LIMIT_EXCEEDED",
            details={"retry_after": retry_after, **(details or {})},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )


class ExternalServiceError(TGOAPIException):
    """External service error exception."""

    def __init__(
        self,
        service: str,
        message: str = "External service error",
        details: Optional[Mapping[str, object]] = None,
    ) -> None:
        super().__init__(
            message=f"{service}: {message}",
            code="EXTERNAL_SERVICE_ERROR",
            details={"service": service, **(details or {})},
            status_code=status.HTTP_502_BAD_GATEWAY,
        )


class _ValidationIssue(TypedDict):
    type: str
    loc: list[str]
    msg: str


_VALIDATION_TYPES: frozenset[str] = frozenset(get_args(ErrorType))
_GENERIC_SERVER_MESSAGE = "An internal error occurred"


def _error_context(
    request: Request, exc: Exception, request_id: str, status_code: int
) -> dict[str, object]:
    bind_error_request_id(request_id)
    route = request.scope.get("route")
    method = request.method
    if method not in {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
        "TRACE",
        "CONNECT",
    }:
        method = "OTHER"
    return {
        "request_id": request_id,
        "path": route.path if isinstance(route, Route) else "unmatched",
        "method": method,
        "status_code": status_code,
        "error_type": type(exc).__name__,
    }


def _log_http_error(
    request: Request, exc: StarletteHTTPException, request_id: str
) -> None:
    if exc.status_code >= 500:
        log = logger.error
    elif exc.status_code == 401:
        path = request.scope.get("path", "")
        log = (
            logger.debug
            if isinstance(path, str)
            and path.startswith("/v1/integrations/wukongim/webhook")
            else logger.info
        )
    else:
        log = logger.warning
    log(
        "HTTP Exception",
        extra=_error_context(request, exc, request_id, exc.status_code),
    )


def _declared_validation_fields(request: Request) -> dict[str, set[str]]:
    route = request.scope.get("route")
    if not isinstance(route, APIRoute):
        return {}
    dependant = route.dependant
    fields = {
        "body": {field.alias for field in dependant.body_params},
        "path": {field.alias for field in dependant.path_params},
        "query": {field.alias for field in dependant.query_params},
        "header": {field.alias for field in dependant.header_params},
        "cookie": {field.alias for field in dependant.cookie_params},
    }
    for field in dependant.body_params:
        model = field.type_
        if isinstance(model, type) and issubclass(model, BaseModel):
            fields["body"].update(
                item.alias if isinstance(item.alias, str) else name
                for name, item in model.model_fields.items()
            )
    return fields


def _safe_validation_issues(
    request: Request, exc: Exception
) -> list[_ValidationIssue]:
    if not isinstance(exc, (RequestValidationError, PydanticValidationError)):
        return [
            {
                "type": "value_error",
                "loc": ["body"],
                "msg": "Invalid field value",
            }
        ]
    fields = _declared_validation_fields(request)
    issues: list[_ValidationIssue] = []
    for raw in exc.errors():
        item: Mapping[str, object] = raw
        kind = item.get("type")
        if not isinstance(kind, str) or kind not in _VALIDATION_TYPES:
            kind = "value_error"
        location = item.get("loc")
        safe_location = ["body"]
        if isinstance(location, (tuple, list)) and location:
            section = location[0]
            if isinstance(section, str) and section in {
                "body",
                "path",
                "query",
                "header",
                "cookie",
            }:
                safe_location = [section]
                if (
                    len(location) > 1
                    and isinstance(location[1], str)
                    and location[1] in fields.get(section, set())
                ):
                    safe_location.append(location[1])
        issues.append(
            {
                "type": kind,
                "loc": safe_location,
                "msg": "Field required"
                if kind == "missing"
                else "Invalid field value",
            }
        )
    return issues


async def tgo_api_exception_handler(
    request: Request, exc: TGOAPIException
) -> JSONResponse:
    """Return controlled domain errors without logging their payloads."""
    request_id = str(uuid4())
    logger.error(
        "TGO API Exception",
        extra=_error_context(request, exc, request_id, exc.status_code),
    )
    server_error = exc.status_code >= 500
    response = ErrorResponse(
        error=ErrorDetail(
            code=exc.code,
            message=_GENERIC_SERVER_MESSAGE if server_error else exc.message,
            details=None if server_error else exc.details,
        ),
        request_id=request_id,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(response.model_dump()),
    )


async def http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Keep public error envelopes and HTTP control headers."""
    request_id = str(uuid4())
    _log_http_error(request, exc, request_id)
    response = ErrorResponse(
        error=ErrorDetail(
            code="HTTP_ERROR",
            message=_GENERIC_SERVER_MESSAGE
            if exc.status_code >= 500
            else str(exc.detail),
            details={"status_code": exc.status_code},
        ),
        request_id=request_id,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=response.model_dump(),
        headers=exc.headers,
    )


async def internal_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    """Preserve private detail envelopes while hiding server exception text."""
    request_id = str(uuid4())
    _log_http_error(request, exc, request_id)
    safe = (
        exc
        if exc.status_code < 500
        else StarletteHTTPException(
            exc.status_code, _GENERIC_SERVER_MESSAGE, headers=exc.headers
        )
    )
    response = await default_http_exception_handler(request, safe)
    response.headers["X-Request-ID"] = request_id
    return response


async def validation_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Report declared field locations without inputs or validator context."""
    request_id = str(uuid4())
    issues = _safe_validation_issues(request, exc)
    context = _error_context(request, exc, request_id, 422)
    context["error_count"] = len(issues)
    logger.warning("Validation Exception", extra=context)
    response = ErrorResponse(
        error=ErrorDetail(
            code="VALIDATION_ERROR",
            message="Request validation failed",
            details={"errors": issues},
        ),
        request_id=request_id,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=response.model_dump(),
    )


async def general_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Return a correlation ID without serializing arbitrary exceptions."""
    request_id = str(uuid4())
    logger.error(
        "Unhandled Exception",
        extra=_error_context(request, exc, request_id, 500),
    )
    response = ErrorResponse(
        error=ErrorDetail(
            code="INTERNAL_ERROR", message=_GENERIC_SERVER_MESSAGE
        ),
        request_id=request_id,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=response.model_dump(),
    )
