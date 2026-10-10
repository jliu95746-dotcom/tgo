"""Staff availability and channel authorization use authenticated tenant scope."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.api.v1.endpoints.knowledge_governance import router as governance_router
from app.core.security import create_access_token
from app.schemas.knowledge_availability import AgentKnowledgeAvailability
from app.services.ai_client import ai_client
from app.services.rag_client import rag_client
from tests.test_platform_tenant_boundary import (  # noqa: F401
    client_for,
    platform_app,
    platform_database,
)


@pytest.fixture
def knowledge_app(platform_app, monkeypatch):
    app, db, companies, staff, platforms = platform_app
    app.include_router(governance_router, prefix="/v1/rag/knowledge-governance")
    report = AgentKnowledgeAvailability(
        project_id=companies[0].id,
        agent_id=uuid4(),
        channel="web",
        binding_mode="project_default",
        collections=[],
        issues=[],
    )
    lookup = AsyncMock(return_value=report)
    update = AsyncMock()
    monkeypatch.setattr(ai_client, "knowledge_availability", lookup)
    monkeypatch.setattr(rag_client, "update_knowledge_channels", update)
    headers = {
        "Authorization": "Bearer "
        + create_access_token(
            staff.username,
            staff.project_id,
            staff.role,
        )
    }
    return app, db, companies, staff, platforms, lookup, update, headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,channel", [("website", "web"), ("wecom", "wecom_kf"), ("phone", "phone")]
)
async def test_default_readiness_uses_actual_platform_channel(
    knowledge_app, kind, channel
):
    app, db, companies, _, platforms, lookup, _, headers = knowledge_app
    platforms[0].type = kind
    db.commit()
    lookup.return_value.channel = channel
    async with client_for(app) as client:
        response = await client.get(
            f"/v1/platforms/{platforms[0].id}/knowledge-availability", headers=headers
        )
    assert response.status_code == 200, response.text
    lookup.assert_awaited_once_with(str(companies[0].id), channel, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", ["current", "explicit", "default"])
async def test_readiness_preview_does_not_change_bindings(knowledge_app, preview):
    app, db, companies, _, platforms, lookup, _, headers = knowledge_app
    bound, candidate = uuid4(), uuid4()
    platforms[0].agent_id = bound
    db.commit()
    params = (
        {"agent_id": str(candidate)}
        if preview == "explicit"
        else {"use_default": True}
        if preview == "default"
        else {}
    )
    selected = (
        candidate if preview == "explicit" else None if preview == "default" else bound
    )
    if selected:
        lookup.return_value.agent_id = selected
    async with client_for(app) as client:
        response = await client.get(
            f"/v1/platforms/{platforms[0].id}/knowledge-availability",
            headers=headers,
            params=params,
        )
    assert response.status_code == 200, response.text
    lookup.assert_awaited_once_with(str(companies[0].id), "web", selected)
    assert platforms[0].agent_id == bound


@pytest.mark.asyncio
async def test_foreign_platform_is_rejected_before_remote_lookup(knowledge_app):
    app, _, _, _, platforms, lookup, _, headers = knowledge_app
    async with client_for(app) as client:
        response = await client.get(
            f"/v1/platforms/{platforms[1].id}/knowledge-availability", headers=headers
        )
    assert response.status_code == 404
    lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["project", "channel", "agent"])
async def test_readiness_rejects_remote_identity_mismatch(knowledge_app, mismatch):
    app, _, companies, _, platforms, lookup, _, headers = knowledge_app
    agent = lookup.return_value.agent_id
    if mismatch == "project":
        lookup.return_value.project_id = companies[1].id
    elif mismatch == "channel":
        lookup.return_value.channel = "wecom_kf"
    else:
        lookup.return_value.agent_id = uuid4()
    async with client_for(app) as client:
        response = await client.get(
            f"/v1/platforms/{platforms[0].id}/knowledge-availability",
            headers=headers,
            params={"agent_id": str(agent)},
        )
    assert response.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("role,expected", [("user", 403), ("admin", 404)])
async def test_channel_edit_requires_admin_and_uses_authenticated_scope(
    knowledge_app, role, expected
):
    from fastapi import HTTPException

    app, db, companies, staff, _, _, update, headers = knowledge_app
    staff.role = role
    db.commit()
    record_id = uuid4()
    update.side_effect = HTTPException(404, "record not found")
    async with client_for(app) as client:
        response = await client.patch(
            f"/v1/rag/knowledge-governance/{record_id}/channels",
            headers=headers,
            json={
                "channels": ["wecom_kf"],
                "expected_updated_at": "2026-10-10T00:00:00Z",
            },
        )
    assert response.status_code == expected, response.text
    if role == "user":
        update.assert_not_awaited()
    else:
        update.assert_awaited_once()
        assert update.await_args.kwargs["project_id"] == str(companies[0].id)
        assert update.await_args.kwargs["record_id"] == str(record_id)
        assert update.await_args.kwargs["data"]["reviewer"] == staff.username


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"channels": []},
        {"channels": ["web", "web"]},
        {"channels": ["web"], "reviewer": "forged"},
        {"channels": ["web"], "expected_updated_at": "2026-10-10T00:00:00"},
    ],
)
async def test_invalid_or_forged_channel_edit_is_rejected(knowledge_app, data):
    app, _, _, _, _, _, update, headers = knowledge_app
    payload = {"expected_updated_at": "2026-10-10T00:00:00Z", **data}
    async with client_for(app) as client:
        response = await client.patch(
            f"/v1/rag/knowledge-governance/{uuid4()}/channels",
            headers=headers,
            json=payload,
        )
    assert response.status_code == 422
    update.assert_not_awaited()
