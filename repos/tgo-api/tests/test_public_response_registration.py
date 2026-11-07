"""Registered tools keep authentication context out of query input."""

from fastapi.routing import APIRoute
import pytest

from app.core.security import get_authenticated_project
from app.main import app


def has_project_authentication(dependency) -> bool:
    return dependency.call is get_authenticated_project or any(
        has_project_authentication(child) for child in dependency.dependencies
    )


@pytest.mark.parametrize(
    "method,path,parameters",
    [
        ("get", "/v1/ai/tools", {"tool_type", "include_deleted"}),
        ("post", "/v1/ai/tools", set()),
        ("post", "/v1/ai/tools/discover", set()),
        ("post", "/v1/ai/tools/{tool_id}/execute", {"tool_id"}),
        ("patch", "/v1/ai/tools/{tool_id}", {"tool_id"}),
        ("delete", "/v1/ai/tools/{tool_id}", {"tool_id"}),
    ],
)
def test_registered_tools_require_project_auth_without_public_context_fields(
    method,
    path,
    parameters,
):
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and method.upper() in route.methods
    )
    assert has_project_authentication(route.dependant)
    operation = app.openapi()["paths"][path][method]
    assert operation["security"]
    assert {
        item["name"] for item in operation.get("parameters", [])
    } == parameters
