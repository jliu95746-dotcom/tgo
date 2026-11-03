"""Public employee mutation identity is current, login-owned and privately forwarded."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints.ai_agents import router
from app.core.database import get_db
from app.core.security import get_authenticated_project, get_current_active_user
from app.models.project import Project
from app.models.staff import Staff
from app.services.ai_client import AIServiceClient, ai_client


def application(role="admin", current_role=None, enabled=True):
    project = SimpleNamespace(id=uuid4(), deleted_at=None)
    actor = SimpleNamespace(id=uuid4(), project_id=project.id, role=role, username="synthetic",
        account_enabled=enabled, deleted_at=None)
    current = SimpleNamespace(**vars(actor))
    if current_role:
        current.role = current_role
    db = Mock()
    db.get.side_effect = lambda kind, identifier, **kwargs: (
        current if kind is Staff and identifier == actor.id else project if kind is Project and identifier == project.id else None)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/agents")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_authenticated_project] = lambda: (project, "synthetic-project-key")
    app.dependency_overrides[get_current_active_user] = lambda: actor
    return app, project, actor


def response(project, agent):
    return {"id": str(agent), "project_id": str(project), "name": "隔离员工", "model": "fixture",
        "is_active": False, "is_default": False, "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat()}


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_mutations_forward_only_login_actor_and_project(monkeypatch, method):
    app, project, actor = application()
    identifier = uuid4()
    operation = AsyncMock(return_value=response(project.id, identifier))
    monkeypatch.setattr(ai_client, "create_agent" if method == "POST" else "update_agent", operation)
    payload = {"name": "隔离员工", "model": "fixture"} if method == "POST" else {"is_active": True}
    path = "/v1/ai/agents" + ("" if method == "POST" else "/" + str(identifier))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        result = await client.request(method, path, json=payload, params={"project_id": str(uuid4())},
            headers={"X-Staff-Id": str(uuid4())})
    assert result.status_code == (201 if method == "POST" else 200)
    assert operation.await_args.kwargs["requested_by"] == actor.id
    assert operation.await_args.kwargs["project_id"] == str(project.id)
    if method == "POST":
        assert operation.await_args.kwargs["agent_data"]["is_active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("fault", ["user", "revoked", "disabled"])
async def test_non_admin_revoked_or_disabled_actor_cannot_mutate(monkeypatch, method, fault):
    app, project, _ = application(role="user" if fault == "user" else "admin",
        current_role="user" if fault == "revoked" else None, enabled=fault != "disabled")
    operation = AsyncMock(return_value=response(project.id, uuid4()))
    monkeypatch.setattr(ai_client, "create_agent" if method == "POST" else "update_agent", operation)
    path = "/v1/ai/agents" + ("" if method == "POST" else "/" + str(uuid4()))
    body = {"name": "隔离员工", "model": "fixture"} if method == "POST" else {"is_active": True}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        result = await client.request(method, path, json=body)
    assert result.status_code == 403
    operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_client_forwards_actor_as_private_identity_without_body_injection(monkeypatch):
    client = AIServiceClient()
    project, actor, identifier = uuid4(), uuid4(), uuid4()
    transport = AsyncMock(return_value=httpx.Response(200, json=response(project, identifier)))
    monkeypatch.setattr(client, "_make_request", transport)
    for operation, arguments in [(client.create_agent, (str(project), {"name": "fixture", "model": "fixture"})),
        (client.update_agent, (str(project), str(identifier), {"is_active": True}))]:
        await operation(*arguments, requested_by=actor)
        request = transport.await_args.kwargs
        assert request["extra_headers"]["X-Staff-Id"] == str(actor)
        assert request["extra_headers"]["X-Project-Id"] == str(project)
        assert request["extra_headers"]["X-Internal-API-Key"]
        assert "requested_by" not in request["json_data"] and "project_id" not in request["json_data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["user", "revoked", "disabled"])
@pytest.mark.parametrize("operation", ["delete", "tool", "collection", "workflow"])
async def test_separate_configuration_routes_cannot_bypass_current_actor(monkeypatch, fault, operation):
    app, _, _ = application(role="user" if fault == "user" else "admin",
        current_role="user" if fault == "revoked" else None, enabled=fault != "disabled")
    method_name = {"delete": "delete_agent", "tool": "set_agent_tool_enabled",
        "collection": "set_agent_collection_enabled", "workflow": "set_agent_workflow_enabled"}[operation]
    action = AsyncMock()
    monkeypatch.setattr(ai_client, method_name, action)
    path = f"/v1/ai/agents/{uuid4()}"
    if operation != "delete":
        path += f"/{operation}s/{uuid4()}/enabled"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        result = await client.request("DELETE" if operation == "delete" else "PATCH", path,
            json=None if operation == "delete" else {"enabled": True})
    assert result.status_code == 403
    action.assert_not_awaited()
