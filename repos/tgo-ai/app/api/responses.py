"""Shared error response helpers for FastAPI route decorators."""

from typing import Dict, Iterable, Optional

from app.schemas.error import Error


ResponseDefinitions = Dict[int | str, Dict[str, object]]


DEFAULT_DESCRIPTIONS: Dict[int, str] = {
    400: "Validation error",
    401: "Authentication failed",
    403: "Access denied",
    404: "Not found",
    409: "Conflict",
    429: "Rate limit exceeded",
}


def build_error_responses(
    codes: Iterable[int],
    overrides: Optional[Dict[int, str]] = None,
) -> ResponseDefinitions:
    """Map status codes to the Error model for FastAPI route decorators.

    Args:
        codes: HTTP status codes to include (e.g., [400, 401, 403]).
        overrides: Optional descriptions to override defaults per status code.

    Returns:
        Dict suitable for the `responses` parameter in route decorators.
    """
    overrides = overrides or {}
    responses: ResponseDefinitions = {}
    for code in codes:
        desc = overrides.get(code, DEFAULT_DESCRIPTIONS.get(code, "Error"))
        responses[code] = {"model": Error, "description": desc}
    return responses
