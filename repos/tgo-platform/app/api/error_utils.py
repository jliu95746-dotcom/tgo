from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ErrorDetail, ErrorResponse


logger = logging.getLogger(__name__)


def get_request_id(request: Request) -> str | None:
    return getattr(getattr(request, "state", object()), "request_id", None)


def default_code_for_status(status_code: int) -> str:
    mapping = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        422: "VALIDATION_ERROR",
        500: "INTERNAL_SERVER_ERROR",
    }
    return mapping.get(status_code, f"ERROR_{status_code}")


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details),
        request_id=request_id,
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    request_id = get_request_id(request)
    code = default_code_for_status(exc.status_code)
    message: str | None = exc.detail if isinstance(exc.detail, str) else None
    details: Any | None = None
    if isinstance(exc.detail, dict):
        code = exc.detail.get("code") or code
        message = exc.detail.get("message") or message or code.replace("_", " ")
        details = exc.detail.get("details")
    if not message:
        message = code.replace("_", " ")
    return error_response(
        exc.status_code,
        code=code,
        message=message,
        details=details,
        request_id=request_id,
    )


async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    request_id = get_request_id(request)
    return error_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details=exc.errors(),
        request_id=request_id,
    )


async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
    request_id = get_request_id(request)
    return error_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="VALIDATION_ERROR",
        message="Validation failed",
        details=exc.errors(),
        request_id=request_id,
    )


async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    request_id = get_request_id(request)
    logger.error("Database request failed: error_type=%s", type(exc).__name__)
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="DATABASE_ERROR",
        message="A database error occurred",
        request_id=request_id,
    )


async def generic_exception_handler(request: Request, exc: Exception):
    request_id = get_request_id(request)
    logger.error("Unexpected request failure: error_type=%s", type(exc).__name__)
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected error occurred",
        request_id=request_id,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(
        RequestValidationError, request_validation_exception_handler
    )
    app.add_exception_handler(ValidationError, pydantic_validation_exception_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
