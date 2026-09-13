"""Tool edits distinguish an omitted field from an explicit clear operation."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import ai_tools
from app.core.security import get_authenticated_project
from app.models import Project
from app.schemas.tools import ToolResponse


@pytest.fixture
def tool_patch_app(monkeypatch: pytest.MonkeyPatch):
    project = Project(id=uuid4(), name="Isolated project", api_key="test-unused")
    tool_id = uuid4()
    now = datetime.now(timezone.utc)
    response = ToolResponse(
        id=tool_id, project_id=project.id, name="lookup", tool_type="MCP",
        description="Previous description", endpoint="https://example.test/mcp",
        created_at=now, updated_at=now,
    )
    update = AsyncMock(side_effect=lambda **kwargs: response.model_copy(
        update=kwargs["tool_data"],
    ))
    monkeypatch.setattr(ai_tools.ai_client, "update_tool", update)
    app = FastAPI()
    app.include_router(ai_tools.router, prefix="/tools")
    app.dependency_overrides[get_authenticated_project] = lambda: (project, "test-unused")
    return app, project, tool_id, update


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"description": None}, {"title": None}, {"title_zh": None},
    {"title_en": None}, {"endpoint": None}, {"config": None},
    {"transport_type": None}, {"description": "New description"}, {},
])
async def test_gateway_forwards_only_requested_fields_including_null(
    tool_patch_app, patch,
):
    app, project, tool_id, update = tool_patch_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        result = await client.patch(f"/tools/{tool_id}", json=patch)
    assert result.status_code == 200
    update.assert_awaited_once_with(
        project_id=str(project.id), tool_id=str(tool_id), tool_data=patch,
    )
    for field, value in patch.items():
        assert result.json()[field] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "tool_type"])
async def test_required_fields_cannot_be_cleared(tool_patch_app, field):
    app, _, tool_id, update = tool_patch_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        result = await client.patch(f"/tools/{tool_id}", json={field: None})
    assert result.status_code == 422
    update.assert_not_awaited()
