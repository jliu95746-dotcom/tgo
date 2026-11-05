"""Every staff authentication path must validate current tenant membership."""

from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import projects
from app.core import security
from app.core.config import settings
from app.core.database import get_db
from app.models import Project, Staff


@pytest.fixture
def boundary_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Project.__table__.create(engine)
    Staff.__table__.create(engine)
    with Session(engine) as db:
        own = Project(name="企业 A", api_key="fixture-project-a")
        other = Project(name="企业 B", api_key="fixture-project-b")
        db.add_all([own, other])
        db.flush()
        staff = Staff(
            project_id=own.id,
            username="boundary@example.com",
            password_hash="unused",
            role="admin",
        )
        db.add(staff)
        db.commit()
        app = FastAPI()
        app.dependency_overrides[get_db] = lambda: db
        app.include_router(projects.router, prefix="/projects")

        @app.get("/staff-check")
        def staff_check(user: Staff = Depends(security.get_current_user)):
            return {"project_id": str(user.project_id)}

        @app.get("/project-check")
        def project_check(context=Depends(security.get_authenticated_project)):
            return {"project_id": str(context[0].id)}

        @app.get("/permission-check")
        def permission_check(
            user: Staff = Depends(security.require_permission("staff:list")),
        ):
            return {"project_id": str(user.project_id)}

        yield app, db, staff, own, other
    engine.dispose()


AUTH_PATHS = ["/staff-check", "/project-check", "/permission-check"]


@pytest.mark.parametrize("change", ["disable", "revoke"])
def test_account_security_changes_revoke_existing_tokens(boundary_app, change):
    _, db, staff, own, _ = boundary_app
    token = security.create_access_token(staff.username, own.id)
    if change == "disable":
        staff.account_enabled = False
    else:
        staff.token_version = 2
    db.commit()
    assert security.resolve_staff_token(db, token) is None


def test_service_pause_does_not_disable_login(boundary_app):
    _, db, staff, own, _ = boundary_app
    staff.is_active = False
    staff.service_paused = True
    db.commit()
    token = security.create_access_token(staff.username, own.id)
    assert security.resolve_staff_token(db, token).id == staff.id


def test_new_token_version_is_accepted_after_password_reset(boundary_app):
    _, db, staff, own, _ = boundary_app
    staff.token_version = 2
    db.commit()
    token = security.create_access_token(
        staff.username, own.id, token_version=2
    )
    assert security.resolve_staff_token(db, token).id == staff.id


@pytest.mark.asyncio
@pytest.mark.parametrize("path", AUTH_PATHS)
@pytest.mark.parametrize(
    "scenario",
    [
        "valid",
        "legacy",
        "mismatch",
        "deleted_staff",
        "deleted_project",
        "missing_staff",
    ],
)
async def test_current_membership_is_enforced(boundary_app, path, scenario):
    app, db, staff, own, other = boundary_app
    subject = staff.username if scenario != "missing_staff" else "absent@example.com"
    claimed_project = other.id if scenario == "mismatch" else own.id
    if scenario == "legacy":
        claimed_project = None
    token = security.create_access_token(subject, claimed_project, role="admin")
    if scenario == "deleted_staff":
        staff.deleted_at = datetime.utcnow()
    if scenario == "deleted_project":
        own.deleted_at = datetime.utcnow()
    db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    if scenario in {"valid", "legacy"}:
        assert response.status_code == 200, response.text
        assert response.json()["project_id"] == str(own.id)
    else:
        assert response.status_code == 401, response.text
    assert other.api_key not in response.text


@pytest.mark.parametrize(
    "purpose",
    ["platform_operator", "email_verification", "password_reset", "plugin_dev"],
)
def test_non_staff_token_purposes_are_rejected(purpose):
    token = jwt.encode(
        {"sub": "boundary@example.com", "project_id": str(uuid4()), "type": purpose},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    assert security.verify_token(token) is None


@pytest.mark.asyncio
async def test_company_admin_cannot_create_another_company(boundary_app):
    app, db, staff, own, _ = boundary_app
    before = db.query(Project).count()
    token = security.create_access_token(staff.username, own.id, role="admin")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/projects",
            json={"name": "Unowned company"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403, response.text
    assert db.query(Project).count() == before


@pytest.mark.asyncio
async def test_permission_denial_does_not_log_username(boundary_app, caplog):
    from app.models import Permission, ProjectRolePermission, RolePermission

    app, db, staff, own, _ = boundary_app
    for model in [Permission, RolePermission, ProjectRolePermission]:
        model.__table__.create(db.bind)
    staff.role = "user"
    db.commit()
    token = security.create_access_token(staff.username, own.id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/permission-check", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 403
    assert staff.username not in caplog.text
    assert "Permission denied" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["failed", "success", "im_failure"])
async def test_login_logs_do_not_include_private_data(
    boundary_app, monkeypatch, caplog, mode
):
    import logging
    from unittest.mock import AsyncMock

    from app.api.v1.endpoints import staff as staff_endpoints

    app, db, staff, own, _ = boundary_app
    app.include_router(staff_endpoints.router, prefix="/staff")
    private_error = "synthetic-private-im-error"
    if mode != "failed":
        staff.password_hash = security.get_password_hash("synthetic-input")
        db.commit()
    synchronize = AsyncMock()
    if mode == "im_failure":
        synchronize.side_effect = RuntimeError(private_error)
    monkeypatch.setattr(
        staff_endpoints.wukongim_client, "register_or_login_user", synchronize
    )
    monkeypatch.setattr(
        staff_endpoints, "ensure_project_staff_channel", AsyncMock()
    )
    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/staff/login",
                data={
                    "username": staff.username, "password": "synthetic-input"
                },
            )
    if mode == "failed":
        assert response.status_code == 401
        synchronize.assert_not_awaited()
    else:
        assert response.status_code == 200
        claims = security.verify_token(response.json()["access_token"])
        assert claims is not None and claims["project_id"] == str(own.id)
    assert staff.username not in caplog.text
    assert private_error not in caplog.text
    for record in caplog.records:
        assert staff.username != getattr(record, "username", None)
        assert private_error != getattr(record, "error", None)
