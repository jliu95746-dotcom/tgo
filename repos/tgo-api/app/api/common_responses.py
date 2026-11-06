"""Common error responses reused by API endpoints."""

from typing import TypeAlias

from app.schemas.base import ErrorResponse

ResponseDescriptions: TypeAlias = dict[int | str, dict[str, object]]

# Common error responses that can be reused across endpoints
COMMON_RESPONSES: ResponseDescriptions = {
    400: {
        "model": ErrorResponse,
        "description": "Bad Request - Invalid request data or parameters",
    },
    401: {
        "model": ErrorResponse,
        "description": "Unauthorized - Authentication required",
    },
    403: {
        "model": ErrorResponse,
        "description": "Forbidden - Access denied to resource",
    },
    404: {
        "model": ErrorResponse,
        "description": "Not Found - Resource not found",
    },
    409: {
        "model": ErrorResponse,
        "description": (
            "Conflict - Resource already exists or conflict with current state"
        ),
    },
    422: {
        "model": ErrorResponse,
        "description": "Unprocessable Entity - Validation error",
    },
    429: {
        "model": ErrorResponse,
        "description": "Too Many Requests - Rate limit exceeded",
    },
    500: {
        "model": ErrorResponse,
        "description": "Internal Server Error - An unexpected error occurred",
    },
    502: {
        "model": ErrorResponse,
        "description": "Bad Gateway - External service error",
    },
}

# Specific response combinations for different endpoint types
AUTH_RESPONSES: ResponseDescriptions = {
    401: COMMON_RESPONSES[401],
    422: COMMON_RESPONSES[422],
}

PROTECTED_RESPONSES: ResponseDescriptions = {
    401: COMMON_RESPONSES[401],
    403: COMMON_RESPONSES[403],
}

CRUD_RESPONSES: ResponseDescriptions = {
    400: COMMON_RESPONSES[400],
    401: COMMON_RESPONSES[401],
    403: COMMON_RESPONSES[403],
    404: COMMON_RESPONSES[404],
    422: COMMON_RESPONSES[422],
}

CREATE_RESPONSES: ResponseDescriptions = {
    400: COMMON_RESPONSES[400],
    401: COMMON_RESPONSES[401],
    403: COMMON_RESPONSES[403],
    409: COMMON_RESPONSES[409],
    422: COMMON_RESPONSES[422],
}

LIST_RESPONSES: ResponseDescriptions = {
    400: COMMON_RESPONSES[400],
    401: COMMON_RESPONSES[401],
    403: COMMON_RESPONSES[403],
}

UPDATE_RESPONSES: ResponseDescriptions = {
    400: COMMON_RESPONSES[400],
    401: COMMON_RESPONSES[401],
    403: COMMON_RESPONSES[403],
    404: COMMON_RESPONSES[404],
    422: COMMON_RESPONSES[422],
}

DELETE_RESPONSES: ResponseDescriptions = {
    401: COMMON_RESPONSES[401],
    403: COMMON_RESPONSES[403],
    404: COMMON_RESPONSES[404],
}
