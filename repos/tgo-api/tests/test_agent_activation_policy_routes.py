"""Only login-scoped administrators may configure employee role/channel policies."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from app.api.v1.endpoints.agent_activation_policies import router
from app.core.exceptions import validation_exception_handler
from app.core.security import get_current_active_user
from app.api.company_configuration_access import require_configuration_write
from app.schemas.agent_activation import AgentActivationPolicies, AgentActivationPolicyChange, AgentActivationPolicyRecord
from app.services.ai_client import AIServiceClient, ai_client


def change():
    return AgentActivationPolicyChange.model_validate({"expected_revision": 0,
        "selection": {"template_id": "knowledge", "template_version": 1, "channel": "web"}})


def record(actor):
    return AgentActivationPolicyRecord(**change().selection.model_dump(), id=uuid4(), revision=1,
        requirements_revision="a" * 64, configured_by=actor, enabled=True)


def application(role="admin"):
    actor = SimpleNamespace(id=uuid4(), project_id=uuid4(), role=role, username="synthetic")
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/agents")
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.dependency_overrides[get_current_active_user] = lambda: actor
    app.dependency_overrides[require_configuration_write] = lambda: None
    return app, actor


@pytest.mark.asyncio
async def test_policy_write_uses_logged_in_identity(monkeypatch):
    app, actor = application()
    agent, result = uuid4(), record(actor.id)
    operation = AsyncMock(return_value=result)
    monkeypatch.setattr(ai_client, "save_agent_activation_policy", operation)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(f"/v1/ai/agents/{agent}/activation-policies/web",
            params={"project_id": str(uuid4())}, headers={"X-Staff-Id": str(uuid4())}, json=change().model_dump(mode="json"))
    assert response.status_code == 200 and response.json()["configured_by"] == str(actor.id)
    assert operation.await_args.args[:3] == (actor.project_id, actor.id, agent)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None), ("PUT", "/web", change().model_dump(mode="json")), ("DELETE", "/web?expected_revision=1", None),
])
async def test_policy_routes_reject_non_admin(method, suffix, body):
    app, _ = application("user")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, f"/v1/ai/agents/{uuid4()}/activation-policies{suffix}", json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_rejected_body_does_not_echo_credentials():
    app, _ = application()
    marker = "private-synthetic-marker"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(f"/v1/ai/agents/{uuid4()}/activation-policies/web",
            json={**change().model_dump(mode="json"), "secret": marker})
    assert response.status_code == 422 and marker not in response.text


@pytest.mark.asyncio
async def test_configuration_entitlement_guard_is_not_bypassed():
    app, _ = application()
    def denied():
        raise HTTPException(403, "当前配置权限不可用")
    app.dependency_overrides[require_configuration_write] = denied
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(f"/v1/ai/agents/{uuid4()}/activation-policies/web", json=change().model_dump(mode="json"))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_client_preserves_scope_and_refuses_wrong_channel_or_forged_approval(monkeypatch):
    project, actor, agent = uuid4(), uuid4(), uuid4()
    client = AIServiceClient()
    result = record(actor)
    transport = AsyncMock(return_value=httpx.Response(200, json=result.model_dump(mode="json")))
    monkeypatch.setattr(client, "_make_request", transport)
    assert await client.save_agent_activation_policy(project, actor, agent, "web", change()) == result
    assert transport.await_args.kwargs["extra_headers"]["X-Project-Id"] == str(project)
    assert transport.await_args.kwargs["extra_headers"]["X-Staff-Id"] == str(actor)
    transport.reset_mock()
    with pytest.raises(HTTPException):
        await client.save_agent_activation_policy(project, actor, agent, "wecom_kf", change())
    transport.assert_not_awaited()
    transport.return_value = httpx.Response(200, json={**result.model_dump(mode="json"), "can_enable": True})
    with pytest.raises(HTTPException) as error:
        await client.save_agent_activation_policy(project, actor, agent, "web", change())
    assert error.value.status_code == 502
    transport.return_value = httpx.Response(200, json=AgentActivationPolicies(
        project_id=uuid4(), agent_id=agent, policies=[result]).model_dump(mode="json"))
    with pytest.raises(HTTPException) as error:
        await client.agent_activation_policies(project, actor, agent)
    assert error.value.status_code == 502
