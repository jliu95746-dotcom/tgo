"""Load legacy annotations without enabling their routes in the application."""

from importlib import import_module

from fastapi import FastAPI
from fastapi.routing import APIRoute
import pytest

from app.core.security import get_authenticated_project


@pytest.mark.parametrize("module_name", ["mcp_tools", "mcp_project_tools"])
def test_legacy_router_annotations_can_register_in_an_isolated_app(
    module_name,
):
    module = import_module("app.api.v1.endpoints." + module_name)
    application = FastAPI()
    application.include_router(module.router, prefix="/isolated")
    schema = application.openapi()
    assert schema["paths"]
    scoped_routes = [
        route
        for route in application.routes
        if isinstance(route, APIRoute)
        and any(
            dependency.call is get_authenticated_project
            for dependency in route.dependant.dependencies
        )
    ]
    assert scoped_routes
    assert all(
        "project_and_api_key"
        not in {
            parameter["name"]
            for operation in schema["paths"][route.path].values()
            for parameter in operation.get("parameters", [])
        }
        for route in scoped_routes
    )
