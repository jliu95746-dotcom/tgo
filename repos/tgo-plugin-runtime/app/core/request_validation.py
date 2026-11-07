"""Declared field locations are useful; request contents remain private."""

from collections.abc import Mapping
from typing import TypedDict, get_args

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from pydantic_core import ErrorType
from starlette.requests import Request

_TYPES = frozenset(
    kind for kind in get_args(ErrorType) if isinstance(kind, str)
)


class ValidationIssue(TypedDict):
    loc: list[str]
    type: str
    msg: str


def safe_validation_issues(
    request: Request,
    error: RequestValidationError,
) -> list[ValidationIssue]:
    fields: dict[str, set[str]] = {}
    route = request.scope.get("route")
    if isinstance(route, APIRoute):
        dependant = route.dependant
        fields = {
            "body": {item.alias for item in dependant.body_params},
            "path": {item.alias for item in dependant.path_params},
            "query": {item.alias for item in dependant.query_params},
            "header": {item.alias for item in dependant.header_params},
            "cookie": {item.alias for item in dependant.cookie_params},
        }
        for item in dependant.body_params:
            model = item.type_
            if isinstance(model, type) and issubclass(model, BaseModel):
                fields["body"].update(
                    field.alias if isinstance(field.alias, str) else name
                    for name, field in model.model_fields.items()
                )
    issues: list[ValidationIssue] = []
    for raw in error.errors():
        issue: Mapping[str, object] = raw
        kind = issue.get("type")
        if not isinstance(kind, str) or kind not in _TYPES:
            kind = "value_error"
        location = issue.get("loc")
        safe = ["body"]
        if isinstance(location, (tuple, list)) and location:
            section = location[0]
            if isinstance(section, str) and section in {
                "body",
                "query",
                "path",
                "header",
                "cookie",
            }:
                safe = [section]
                if (
                    len(location) > 1
                    and isinstance(location[1], str)
                    and location[1] in fields.get(section, set())
                ):
                    safe.append(location[1])
        issues.append(
            {
                "loc": safe,
                "type": kind,
                "msg": "Field required"
                if kind == "missing"
                else "Invalid field value",
            }
        )
    return issues


async def safe_request_validation(
    request: Request,
    error: Exception,
) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    return JSONResponse(
        status_code=422,
        content={
            "detail": safe_validation_issues(request, error),
        },
    )
