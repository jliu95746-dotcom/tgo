"""Activation checks are login-scoped reads with strictly validated receipts."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.schemas.agent_activation_readiness import AgentActivationReadiness
from app.services.ai_client import AIServiceClient, ai_client
from tests.test_agent_activation_policy_routes import application


def receipt(project, actor, agent):
    return AgentActivationReadiness(project_id=project, agent_id=agent, checked_by=actor,
        checked_at=datetime.now(UTC), enabled=False, can_enable=True,
        configuration_fingerprint="a" * 64, model_owner_revision="b" * 64,
        channels=[{"template_id": "knowledge", "template_version": 1, "channel": "web",
            "policy_revision": 1, "requirements_revision": "c" * 64, "status": "passed",
            "valid_until": datetime.now(UTC) + timedelta(hours=1)}])


@pytest.mark.asyncio
async def test_readiness_uses_login_identity_and_does_not_activate(monkeypatch):
    app, actor = application()
    agent = uuid4()
    operation = AsyncMock(return_value=receipt(actor.project_id, actor.id, agent))
    monkeypatch.setattr(ai_client, "agent_activation_readiness", operation)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/ai/agents/{agent}/activation-readiness",
            params={"project_id": str(uuid4()), "can_enable": True}, headers={"X-Staff-Id": str(uuid4())})
    assert response.status_code == 200 and response.json()["enabled"] is False
    assert operation.await_args.args == (actor.project_id, actor.id, agent)


@pytest.mark.asyncio
async def test_readiness_rejects_non_admin():
    app, _ = application("user")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/ai/agents/{uuid4()}/activation-readiness")
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["project", "actor", "agent", "scope", "empty", "expired", "secret", "blocked", "stale", "business_without_evidence"])
async def test_client_rejects_wrong_identity_or_incomplete_success(monkeypatch, fault):
    project, actor, agent = uuid4(), uuid4(), uuid4()
    client = AIServiceClient()
    body = receipt(project, actor, agent).model_dump(mode="json")
    if fault in {"project", "actor", "agent"}:
        field = {"project": "project_id", "actor": "checked_by", "agent": "agent_id"}[fault]
        body[field] = str(uuid4())
    elif fault == "scope":
        body["scope"] = "knowledge_templates"
    elif fault == "empty":
        body["channels"] = []
    elif fault == "expired":
        body["channels"][0]["valid_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif fault == "secret":
        body["api_key"] = "synthetic-private-marker"
    elif fault == "stale":
        body["checked_at"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    elif fault == "business_without_evidence":
        body["channels"][0]["template_id"] = "logistics"
    else:
        body["blockers"] = [{"code": "owner_unavailable", "message": "无法检查"}]
    transport = AsyncMock(return_value=httpx.Response(200, json=body))
    monkeypatch.setattr(client, "_make_request", transport)
    with pytest.raises(HTTPException) as error:
        await client.agent_activation_readiness(project, actor, agent)
    assert error.value.status_code == 502
    assert transport.await_args.kwargs["extra_headers"]["X-Staff-Id"] == str(actor)


@pytest.mark.asyncio
async def test_client_current_receipt_and_owner_unavailability(monkeypatch):
    project, actor, agent = uuid4(), uuid4(), uuid4()
    client = AIServiceClient()
    result = receipt(project, actor, agent)
    transport = AsyncMock(return_value=httpx.Response(200, json=result.model_dump(mode="json")))
    monkeypatch.setattr(client, "_make_request", transport)
    assert await client.agent_activation_readiness(project, actor, agent) == result
    transport.return_value = httpx.Response(503, json={"detail": "synthetic-private-marker"})
    with pytest.raises(HTTPException) as error:
        await client.agent_activation_readiness(project, actor, agent)
    assert error.value.status_code == 503 and "synthetic-private-marker" not in str(error.value.detail)
