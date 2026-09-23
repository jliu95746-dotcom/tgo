"""AI employee bindings must be checked before persisting channel changes."""

from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app.core.security import create_access_token
from app.models import Platform
from app.services.ai_client import ai_client
from tests.test_platform_tenant_boundary import (  # noqa: F401
    client_for,
    platform_app,
    platform_database,
)


@pytest.fixture
def binding_app(request, monkeypatch):
    app, db, companies, staff, platforms = request.getfixturevalue(
        "platform_app"
    )
    agent_id = uuid4()
    lookup = AsyncMock(
        return_value={"id": str(agent_id)}
    )
    monkeypatch.setattr(ai_client, "get_agent", lookup)
    token = create_access_token(staff.username, staff.project_id, staff.role)
    return (
        app,
        db,
        companies,
        platforms,
        agent_id,
        lookup,
        {"Authorization": f"Bearer {token}"},
    )


async def change_binding(client, platform_id, agent_id, operation, headers):
    if operation == "create":
        return await client.post(
            "/v1/platforms",
            headers=headers,
            json={"name": "新渠道", "type": "website", "agent_id": str(agent_id)},
        )
    field = "agent_ids" if operation == "legacy" else "agent_id"
    value = [str(agent_id)] if operation == "legacy" else str(agent_id)
    return await client.patch(
        f"/v1/platforms/{platform_id}", headers=headers, json={field: value}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "patch", "legacy"])
@pytest.mark.parametrize(
    "result,expected",
    [
        ("not_found", 404),
        ("foreign_identity", 404),
        ("wrong_id", 404),
        ("malformed", 502),
        ("timeout", 504),
    ],
)
async def test_unverified_agent_never_reaches_database(
    binding_app, operation, result, expected
):
    app, db, companies, platforms, agent_id, lookup, headers = binding_app
    own = platforms[0]
    original = (own.agent_id, own.updated_at, db.query(Platform).count())
    if result in ("not_found", "timeout"):
        lookup.side_effect = HTTPException(
            404 if result == "not_found" else 504, "synthetic failure"
        )
    elif result == "foreign_identity":
        lookup.return_value = {
            "id": str(agent_id),
            "project_id": str(companies[1].id),
        }
    elif result == "wrong_id":
        lookup.return_value = {
            "id": str(uuid4()),
            "project_id": str(companies[0].id),
        }
    else:
        lookup.return_value = {"id": "invalid-uuid"}
    async with client_for(app) as client:
        response = await change_binding(
            client, own.id, agent_id, operation, headers
        )
    assert response.status_code == expected, response.text
    assert original == (
        own.agent_id,
        own.updated_at,
        db.query(Platform).count(),
    )
    lookup.assert_awaited_once_with(
        project_id=str(companies[0].id),
        agent_id=str(agent_id),
        include_tools=False,
        include_collections=False,
        include_workflows=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "patch", "legacy"])
async def test_own_agent_is_checked_and_persisted(binding_app, operation):
    app, db, _, platforms, agent_id, lookup, headers = binding_app
    async with client_for(app) as client:
        response = await change_binding(
            client, platforms[0].id, agent_id, operation, headers
        )
    assert response.status_code == (
        201 if operation == "create" else 200
    ), response.text
    assert response.json()["agent_id"] == str(agent_id)
    lookup.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    ["enable", "enable-ai", "patch-active", "patch-auto", "patch-assist"],
)
async def test_reactivation_rechecks_existing_binding(binding_app, operation):
    app, db, _, platforms, agent_id, lookup, headers = binding_app
    own = platforms[0]
    own.agent_id, own.is_active, own.ai_mode = agent_id, False, "off"
    db.commit()
    lookup.side_effect = HTTPException(404, "synthetic missing employee")
    async with client_for(app) as client:
        path = f"/v1/platforms/{own.id}"
        if operation in ("enable", "enable-ai"):
            response = await client.post(
                f"{path}/{operation}", headers=headers
            )
        else:
            changes = {
                "patch-active": {"is_active": True},
                "patch-auto": {"ai_mode": "auto"},
                "patch-assist": {"ai_mode": "assist"},
            }
            response = await client.patch(
                path, headers=headers, json=changes[operation]
            )
    assert response.status_code == 404, response.text
    assert (
        own.agent_id == agent_id and not own.is_active and own.ai_mode == "off"
    )
    lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_binding_and_cosmetic_edits_work_during_ai_outage(
    binding_app,
):
    app, db, _, platforms, agent_id, lookup, headers = binding_app
    own = platforms[0]
    own.agent_id = agent_id
    db.commit()
    lookup.side_effect = HTTPException(504, "synthetic outage")
    async with client_for(app) as client:
        path = f"/v1/platforms/{own.id}"
        for body in ({"name": "改名"}, {"ai_mode": "off"}, {"agent_id": None}):
            response = await client.patch(path, headers=headers, json=body)
            assert response.status_code == 200, response.text
    assert own.agent_id is None
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_platform_is_rejected_before_ai_lookup(binding_app):
    app, _, _, platforms, agent_id, lookup, headers = binding_app
    async with client_for(app) as client:
        response = await change_binding(
            client, platforms[1].id, agent_id, "patch", headers
        )
    assert response.status_code == 404
    lookup.assert_not_awaited()
