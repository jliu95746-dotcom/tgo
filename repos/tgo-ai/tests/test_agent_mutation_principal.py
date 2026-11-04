"""An API key or browser-supplied staff identifier cannot become a mutation actor."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.agents import router
from app.config import settings
from app.dependencies import get_agent_service
from app.models.agent import Agent


def application(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "synthetic-mutation-internal-key")
    project, actor, identifier = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    result = Agent(id=identifier, project_id=project, name="隔离员工", model="fixture",
        instruction="隔离职责", is_active=False, is_default=False, skills_enabled=False,
        agent_category="normal", is_remote_store_agent=False, humanization_skill_enabled=False, created_at=now, updated_at=now,
        collections=[], workflows=[], tools=[])
    service = SimpleNamespace(create_agent=AsyncMock(return_value=result), update_agent=AsyncMock(return_value=result))
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/agents")
    app.dependency_overrides[get_agent_service] = lambda: service
    headers = {"X-Internal-API-Key": "synthetic-mutation-internal-key", "X-Project-Id": str(project), "X-Staff-Id": str(actor)}
    return app, project, actor, identifier, service, headers


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_trusted_actor_is_forwarded_only_after_private_scope_validation(monkeypatch, method):
    app, project, actor, identifier, service, headers = application(monkeypatch)
    path = "/api/v1/agents" + ("" if method == "POST" else "/" + str(identifier))
    payload = {"name": "隔离员工", "model": "fixture"} if method == "POST" else {"is_active": True}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.request(method, path, params={"project_id": str(project)}, headers=headers, json=payload)
    assert response.status_code in {200, 201}
    operation = service.create_agent if method == "POST" else service.update_agent
    assert operation.await_args.kwargs["requested_by"] == actor


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["key", "missing_key", "project", "actor", "missing_actor"])
async def test_forged_or_incomplete_identity_never_reaches_the_business_service(monkeypatch, fault):
    app, project, _, identifier, service, headers = application(monkeypatch)
    if fault == "key":
        headers["X-Internal-API-Key"] = "synthetic-private-marker"
    elif fault == "missing_key":
        del headers["X-Internal-API-Key"]
    elif fault == "project":
        headers["X-Project-Id"] = str(uuid4())
    elif fault == "actor":
        headers["X-Staff-Id"] = "invalid-actor"
    else:
        del headers["X-Staff-Id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.patch(f"/api/v1/agents/{identifier}", params={"project_id": str(project)},
            headers=headers, json={"is_active": True})
    assert response.status_code == 403 and "synthetic-private-marker" not in response.text
    service.update_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_paused_save_has_no_actor_and_payload_cannot_inject_one(monkeypatch):
    app, project, actor, _, service, _ = application(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/v1/agents", params={"project_id": str(project)},
            json={"name": "隔离员工", "model": "fixture"})
        assert response.status_code in {200, 201}
        assert service.create_agent.await_args.kwargs["requested_by"] is None
        response = await client.post("/api/v1/agents", params={"project_id": str(project)},
            json={"name": "隔离员工", "model": "fixture", "requested_by": str(actor)})
        assert response.status_code == 422
    assert service.create_agent.await_count == 1
