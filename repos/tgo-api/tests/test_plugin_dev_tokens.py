"""Plugin debug credentials stay tenant-scoped and separate from staff login."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI
from jose import jwt

from app.api.v1.endpoints import plugins
from app.core import security
from app.core.config import settings
from app.core.database import get_db
from app.models import Staff


@pytest.fixture
def token_app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Staff]:
    monkeypatch.setattr(settings, "SECRET_KEY", "isolated-plugin-token-test-key")
    staff = Staff(
        id=uuid4(), project_id=uuid4(), username="token-test-admin",
        password_hash="unused", role="admin",
    )
    app = FastAPI()
    app.include_router(plugins.router, prefix="/plugins")
    app.dependency_overrides[security.get_current_active_user] = lambda: staff

    # This is the same authentication dependency used by project-scoped APIs.
    # A debug token must be rejected before any database lookup takes place.
    def forbidden_database():
        class NoDatabase:
            def query(self, *args):
                pytest.fail("Debug token reached the project database")
        yield NoDatabase()

    app.dependency_overrides[get_db] = forbidden_database

    @app.get("/project-check")
    async def project_check(project=Depends(security.get_authenticated_project)):
        return {"ok": True}

    return app, staff


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [None, 1, 24])
async def test_admin_receives_bounded_token_for_own_project(token_app, hours):
    app, staff = token_app
    body = {"project_id": str(staff.project_id)}
    if hours is not None:
        body["expires_hours"] = hours
    before = datetime.now(timezone.utc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post("/plugins/dev-token", json=body)
    assert response.status_code == 200
    result = response.json()
    payload = jwt.decode(result["token"], settings.SECRET_KEY, algorithms=["HS256"])
    assert payload["project_id"] == str(staff.project_id)
    assert payload["user_id"] == str(staff.id)
    assert payload["type"] == "plugin_dev"
    assert "sub" not in payload
    expires_at = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
    assert expires_at.tzinfo is not None
    duration = timedelta(hours=hours or 24)
    assert before + duration <= expires_at <= datetime.now(timezone.utc) + duration
    assert payload["exp"] == int(expires_at.timestamp())


@pytest.mark.asyncio
async def test_admin_cannot_mint_a_token_for_another_project(token_app):
    app, _ = token_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post("/plugins/dev-token", json={
            "project_id": str(uuid4()),
        })
    assert response.status_code == 403
    assert "token" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "agent"])
async def test_non_admin_cannot_create_debug_credentials(token_app, role):
    app, staff = token_app
    staff.role = role
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post("/plugins/dev-token", json={
            "project_id": str(staff.project_id),
        })
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [-1, 0, 25, 10**9, True, 1.5, "24", None])
async def test_invalid_expiration_is_rejected(token_app, hours):
    app, staff = token_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post("/plugins/dev-token", json={
            "project_id": str(staff.project_id), "expires_hours": hours,
        })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_debug_token_is_not_a_staff_access_token(token_app):
    app, staff = token_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        issued = await client.post("/plugins/dev-token", json={
            "project_id": str(staff.project_id),
        })
        assert issued.status_code == 200
        token = issued.json()["token"]
        response = await client.get(
            "/project-check", headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401
    assert security.verify_token(token) is None


@pytest.mark.asyncio
async def test_anonymous_cannot_create_debug_credentials(token_app):
    app, staff = token_app
    app.dependency_overrides.pop(security.get_current_active_user)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post("/plugins/dev-token", json={
            "project_id": str(staff.project_id),
        })
    assert response.status_code in {401, 403}


def test_existing_login_tokens_still_work(token_app):
    _, staff = token_app
    token = security.create_access_token(
        staff.username, project_id=staff.project_id, role=staff.role,
    )
    payload = security.verify_token(token)
    assert payload is not None
    assert payload["sub"] == staff.username
    assert payload["project_id"] == str(staff.project_id)


def test_openapi_documents_lifetime_limits(token_app):
    app, _ = token_app
    field = app.openapi()["components"]["schemas"]["DevTokenRequest"][
        "properties"
    ]["expires_hours"]
    assert field["minimum"] == 1
    assert field["maximum"] == 24
    assert field["default"] == 24
