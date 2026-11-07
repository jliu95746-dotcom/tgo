"""The tools gateway derives ownership from its authenticated project."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import ai_tools
from app.core.security import get_authenticated_project
from app.schemas.tools import ToolResponse


@pytest.fixture
def tools_gateway(monkeypatch):
    project = SimpleNamespace(id=uuid4())
    tool_id = uuid4()
    now = datetime.now(timezone.utc)
    row = ToolResponse(
        id=tool_id,
        project_id=project.id,
        name="lookup",
        tool_type="MCP",
        endpoint="https://example.test/mcp",
        created_at=now,
        updated_at=now,
    ).model_dump(mode="json")
    operations = {
        "list_tools": AsyncMock(return_value=[row]),
        "create_tool": AsyncMock(return_value=row),
        "delete_tool": AsyncMock(return_value=row),
    }
    for name, operation in operations.items():
        monkeypatch.setattr(ai_tools.ai_client, name, operation)
    app = FastAPI()
    app.include_router(ai_tools.router, prefix="/tools")
    app.dependency_overrides[get_authenticated_project] = lambda: (
        project,
        "synthetic-key",
    )
    return SimpleNamespace(
        app=app,
        project=project,
        tool_id=tool_id,
        row=row,
        operations=operations,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected",
    [
        (None, None),
        ("MCP", "MCP"),
        ("FUNCTION", "FUNCTION"),
        ("ALL", None),
    ],
)
async def test_list_tools_keeps_filters_and_authenticated_ownership(
    tools_gateway,
    kind,
    expected,
):
    case = tools_gateway
    parameters = {"project_id": str(uuid4()), "include_deleted": "true"}
    if kind is not None:
        parameters["tool_type"] = kind
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/tools", params=parameters)
    assert response.status_code == 200
    assert response.json() == [case.row]
    case.operations["list_tools"].assert_awaited_once_with(
        project_id=str(case.project.id),
        tool_type=expected,
        include_deleted=True,
    )


@pytest.mark.asyncio
async def test_create_tool_uses_server_project_instead_of_body_owner(
    tools_gateway,
):
    case = tools_gateway
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/tools",
            json={
                "name": "lookup",
                "tool_type": "MCP",
                "endpoint": "https://example.test/mcp",
                "project_id": str(uuid4()),
            },
        )
    assert response.status_code == 201
    assert response.json() == case.row
    operation = case.operations["create_tool"]
    operation.assert_awaited_once()
    assert operation.await_args.kwargs["tool_data"]["project_id"] == str(
        case.project.id
    )
    assert operation.await_args.kwargs["tool_data"]["name"] == "lookup"


@pytest.mark.asyncio
async def test_delete_tool_keeps_authenticated_project_and_response(
    tools_gateway,
):
    case = tools_gateway
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app),
        base_url="http://test",
    ) as client:
        response = await client.delete(
            f"/tools/{case.tool_id}",
            params={"project_id": str(uuid4())},
        )
    assert response.status_code == 200
    assert response.json() == case.row
    case.operations["delete_tool"].assert_awaited_once_with(
        project_id=str(case.project.id),
        tool_id=str(case.tool_id),
    )
