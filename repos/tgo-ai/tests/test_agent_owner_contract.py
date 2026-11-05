"""Employee JSON must retain its server-owned project for gateway checks."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.v1.agents import router
from app.dependencies import get_agent_service
from app.models.agent import Agent
from app.schemas.agent import AgentResponse, AgentWithDetails


def stored_agent() -> Agent:
    now = datetime.now(timezone.utc)
    return Agent(
        id=uuid4(),
        project_id=uuid4(),
        name="企业测试员工",
        model="fixture",
        created_at=now,
        updated_at=now,
        is_default=False,
        is_active=False,
        is_remote_store_agent=False,
        agent_category="normal",
        skills_enabled=True,
        humanization_skill_enabled=False,
    )


@pytest.mark.parametrize("schema", [AgentResponse, AgentWithDetails])
def test_json_keeps_project_from_the_stored_employee(schema) -> None:
    stored = stored_agent()
    response = schema.model_validate(stored)
    data = response.model_dump(mode="json", by_alias=True)
    assert data["id"] == str(stored.id)
    assert data["project_id"] == str(stored.project_id)
    assert "projectId" not in data


@pytest.mark.parametrize("schema", [AgentResponse, AgentWithDetails])
@pytest.mark.parametrize("owner", [None, "invalid-id", "missing"])
def test_response_cannot_silently_omit_or_invent_owner(schema, owner) -> None:
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid4(),
        "name": "企业测试员工",
        "model": "fixture",
        "created_at": now,
        "updated_at": now,
    }
    if owner != "missing":
        values["project_id"] = owner
    with pytest.raises(ValidationError):
        schema.model_validate(values)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_request_project", [True, False])
async def test_detail_http_response_keeps_stored_owner(
    same_request_project: bool,
) -> None:
    # AgentService owns query filtering, which is covered separately.
    # A foreign record must not be relabeled as the requested project.
    stored = stored_agent()
    requested = stored.project_id if same_request_project else uuid4()
    service = AsyncMock()
    service.get_agent.return_value = stored
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/agents")
    app.dependency_overrides[get_agent_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        result = await client.get(
            f"/api/v1/agents/{stored.id}",
            params={"project_id": str(requested), "include_tools": "false"},
        )
    assert result.status_code == 200
    assert result.json()["project_id"] == str(stored.project_id)
    service.get_agent.assert_awaited_once_with(requested, stored.id)
