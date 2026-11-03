"""Private staff records must remain authorized until response bytes are sent."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.api.v1.endpoints import visitors as endpoints
from app.core.config import settings
from app.core.security import create_access_token
from app.models import (
    VisitorAIProfile, VisitorAIInsight, VisitorSystemInfo, VisitorTag, Tag,
    Permission, RolePermission, ProjectRolePermission,
)
from app.schemas.visitor import VisitorResponse, VisitorBasicResponse
from tests.test_im_access import records as records
from tests.test_visitor_profile_access import profile_api as profile_api


@pytest.fixture
def staff_read_api(profile_api, monkeypatch):
    app, db, projects, staff, platforms, visitors, extra_staff, _, _, _ = profile_api
    for model in (VisitorAIProfile, VisitorAIInsight, VisitorSystemInfo, VisitorTag, Tag, Permission, RolePermission, ProjectRolePermission):
        model.__table__.create(db.get_bind())
    visitors[0].note = "staff-private-note"
    db.commit()
    style = AsyncMock(return_value={"skill_name": "private-style", "enabled": True, "source": "conversation", "agent_id": None, "agent_name": None})
    monkeypatch.setattr(endpoints, "resolve_employee_style", style)
    return app, db, projects, staff, platforms, visitors, extra_staff, style


def proof(staff):
    return {"Authorization": "Bearer " + create_access_token(staff.username, staff.project_id, staff.role)}


async def get(app, path, headers, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, headers=headers, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["missing", "invalid", "foreign"])
async def test_details_do_not_generate_fake_customer(staff_read_api, identifier):
    app, _, _, staff, _, visitors, _, _ = staff_read_api
    value = str(uuid4()) if identifier == "missing" else "not-a-customer" if identifier == "invalid" else str(visitors[1].id)
    response = await get(app, "/visitors/" + value, proof(staff[0]))
    assert response.status_code == 404
    assert "default_" not in response.text and "ai_settings" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["detail", "channel", "list"])
async def test_owned_staff_private_record_and_no_store(staff_read_api, path):
    app, _, _, staff, _, visitors, _, _ = staff_read_api
    target = "/visitors/" + str(visitors[0].id) if path == "detail" else "/visitors/by-channel" if path == "channel" else "/visitors"
    params = {"channel_id": str(visitors[0].id) + "-vtr", "channel_type": 251} if path == "channel" else {}
    response = await get(app, target, proof(staff[0]), params=params)
    assert response.status_code == 200
    assert "staff-private-note" in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["detail", "channel", "list"])
async def test_revoked_staff_at_serialization_cannot_receive_private_record(staff_read_api, monkeypatch, path):
    app, db, _, staff, _, visitors, _, _ = staff_read_api
    original = VisitorResponse.model_validate

    def revoke(value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        staff[0].account_enabled = False
        db.commit()
        return result

    monkeypatch.setattr(VisitorResponse, "model_validate", revoke)
    target = "/visitors/" + str(visitors[0].id) if path == "detail" else "/visitors/by-channel" if path == "channel" else "/visitors"
    params = {"channel_id": str(visitors[0].id) + "-vtr", "channel_type": 251} if path == "channel" else {}
    response = await get(app, target, proof(staff[0]), params=params)
    assert response.status_code == 401
    assert "staff-private-note" not in response.text


@pytest.mark.asyncio
async def test_humanization_rechecks_authority_after_upstream(staff_read_api):
    app, db, _, staff, _, visitors, _, style = staff_read_api

    async def revoke(*_args, **_kwargs):
        staff[0].token_version += 1
        db.commit()
        return style.return_value

    style.side_effect = revoke
    response = await get(app, f"/visitors/{visitors[0].id}/humanization-settings", proof(staff[0]))
    assert response.status_code == 401 and "private-style" not in response.text


@pytest.mark.asyncio
async def test_channel_lookup_enforces_current_staff_assignment(staff_read_api, monkeypatch):
    app, db, _, _, _, visitors, unassigned, _ = staff_read_api
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    permission = Permission(resource="visitors", action="read", description="读取客户")
    db.add(permission)
    db.flush()
    db.add(RolePermission(role=unassigned.role, permission_id=permission.id))
    db.commit()
    response = await get(app, "/visitors/by-channel", proof(unassigned), params={"channel_id": str(visitors[0].id) + "-vtr", "channel_type": 251})
    assert response.status_code == 404 and "staff-private-note" not in response.text


@pytest.mark.asyncio
async def test_removed_read_permission_blocks_serialized_record(staff_read_api, monkeypatch):
    app, db, _, staff, _, visitors, _, _ = staff_read_api
    staff[0].role = "user"
    permission = Permission(resource="visitors", action="read")
    db.add(permission)
    db.flush()
    grant = RolePermission(role="user", permission_id=permission.id)
    db.add(grant)
    db.commit()
    original = VisitorResponse.model_validate

    def revoke(value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        db.delete(grant)
        db.commit()
        return result

    monkeypatch.setattr(VisitorResponse, "model_validate", revoke)
    response = await get(app, f"/visitors/{visitors[0].id}", proof(staff[0]))
    assert response.status_code == 403 and "staff-private-note" not in response.text


@pytest.mark.asyncio
async def test_humanization_foreign_customer_never_calls_upstream(staff_read_api):
    app, _, _, staff, _, visitors, _, style = staff_read_api
    response = await get(app, f"/visitors/{visitors[1].id}/humanization-settings", proof(staff[0]))
    assert response.status_code == 404
    style.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing_permission", "revoked_permission"])
@pytest.mark.parametrize("target", ["channel", "basic"])
async def test_staff_channel_metadata_requires_current_read_permission(staff_read_api, monkeypatch, change, target):
    app, db, _, staff, _, visitors, _, _ = staff_read_api
    staff[0].role = "user"
    response_model = VisitorResponse if target == "channel" else VisitorBasicResponse
    original = response_model.model_validate
    if change == "revoked_permission":
        permission = Permission(resource="visitors", action="read")
        db.add(permission)
        db.flush()
        grant = RolePermission(role="user", permission_id=permission.id)
        db.add(grant)

        def revoke(value, *args, **kwargs):
            result = original(value, *args, **kwargs)
            db.delete(grant)
            db.commit()
            return result

        monkeypatch.setattr(response_model, "model_validate", revoke)
    db.commit()
    target_path = "/channels/info" if target == "channel" else f"/visitors/{visitors[0].id}/basic"
    params = {"channel_id": str(visitors[0].id) + "-vtr", "channel_type": 251} if target == "channel" else {}
    response = await get(app, target_path, proof(staff[0]), params=params)
    assert response.status_code == 403 and "staff-private-note" not in response.text
