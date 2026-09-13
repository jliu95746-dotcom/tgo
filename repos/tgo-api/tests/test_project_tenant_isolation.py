"""Project credentials and writes must remain inside the authenticated tenant."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import projects
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models import Project, Staff


@pytest.fixture
def project_app():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Project.__table__.create(engine)
    with Session(engine) as db:
        own = Project(id=uuid4(), name="Owned project", api_key="owned-fixture-key")
        other = Project(id=uuid4(), name="Other project", api_key="other-fixture-key")
        db.add_all([own, other])
        db.commit()
        staff = Staff(
            id=uuid4(), username="tenant-admin", project_id=own.id,
            password_hash="unused", role="admin",
        )
        app = FastAPI()
        app.include_router(projects.router, prefix="/projects")
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_active_user] = lambda: staff
        yield app, db, staff, own, other
    engine.dispose()


@pytest.mark.asyncio
async def test_list_projects_never_exposes_another_tenants_key(project_app):
    app, _, _, own, other = project_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get("/projects")
    assert result.status_code == 200
    assert [row["id"] for row in result.json()["data"]] == [str(own.id)]
    assert other.api_key not in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_admin_cannot_read_or_change_another_project(project_app, method):
    app, db, _, _, other = project_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.request(
            method, f"/projects/{other.id}",
            **({"json": {"name": "unauthorized"}} if method == "PATCH" else {}),
        )
    assert result.status_code == 404
    assert other.api_key not in result.text
    db.refresh(other)
    assert other.name == "Other project" and other.deleted_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None),
    ("GET", "/{id}", None),
    ("POST", "", {"name": "unauthorized"}),
    ("PATCH", "/{id}", {"name": "unauthorized"}),
    ("DELETE", "/{id}", None),
    ("PUT", "/{id}/ai-config", {}),
    ("POST", "/{id}/ai-config/sync", None),
])
async def test_members_cannot_get_keys_or_change_project_settings(
    project_app, method, suffix, body,
):
    app, _, staff, own, _ = project_app
    staff.role = "user"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.request(
            method, "/projects" + suffix.format(id=own.id),
            **({"json": body} if body is not None else {}),
        )
    assert result.status_code == 403
    assert own.api_key not in result.text


@pytest.mark.asyncio
async def test_admin_can_read_and_rename_their_own_project(project_app):
    app, db, _, own, other = project_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get(f"/projects/{own.id}")
        assert result.status_code == 200 and result.json()["id"] == str(own.id)
        result = await client.patch(f"/projects/{own.id}", json={"name": "Renamed"})
        assert result.status_code == 200 and result.json()["name"] == "Renamed"
    db.refresh(other)
    assert other.name == "Other project"
