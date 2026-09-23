"""Channel management and visitor information have different trust scopes."""

from datetime import datetime, timezone
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.platforms import router
from app.core.database import get_db
from app.core.security import create_access_token
from app.models import (
    Permission,
    Platform,
    PlatformTypeDefinition,
    Project,
    ProjectRolePermission,
    RolePermission,
    Staff,
)


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def platform_database():
    url = os.getenv("SAAS_PLATFORM_TEST_DATABASE_URL")
    if not url:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            yield engine
        finally:
            engine.dispose()
        return
    engine = create_engine(
        url.replace("postgresql+asyncpg://", "postgresql+psycopg2://"),
        connect_args={"connect_timeout": 5},
    )
    schema = "platform_boundary_" + uuid4().hex
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                connection.execute(
                    text(f'SET LOCAL search_path TO "{schema}"')
                )
                yield connection
            finally:
                transaction.rollback()
        with engine.connect() as check:
            remaining = check.execute(
                text("SELECT count(*) FROM pg_namespace WHERE nspname=:name"),
                {"name": schema},
            ).scalar_one()
            assert remaining == 0, "Private test schema was not rolled back"
    finally:
        engine.dispose()


@pytest.fixture
def platform_app(platform_database):
    for model in (
        Project,
        Staff,
        PlatformTypeDefinition,
        Platform,
        Permission,
        RolePermission,
        ProjectRolePermission,
    ):
        model.__table__.create(platform_database)
    with Session(
        platform_database, join_transaction_mode="create_savepoint"
    ) as db:
        companies = [
            Project(name=name, api_key="project-" + name)
            for name in ("A", "B")
        ]
        db.add_all(companies)
        db.flush()
        staff = Staff(
            project_id=companies[0].id,
            username="channel-check",
            role="admin",
            password_hash="unused",
        )
        platforms = [
            Platform(
                project_id=c.id,
                name=c.name,
                type="website",
                api_key="platform-" + c.name,
                is_active=True,
                config={
                    "widget_title": "域见客服",
                    "theme_color": "#123456",
                    "display_mode": "big",
                    "app_secret": "synthetic-private-value",
                    "unknown_future_credential": {
                        "value": "synthetic-nested-secret"
                    },
                },
            )
            for c in companies
        ]
        db.add_all([staff, *platforms])
        for action in ("list", "read", "update", "delete", "create"):
            permission = Permission(resource="platforms", action=action)
            db.add(permission)
            db.flush()
            db.add(RolePermission(role="user", permission_id=permission.id))
        db.commit()
        app = FastAPI()
        app.dependency_overrides[get_db] = lambda: db
        app.include_router(router, prefix="/v1/platforms")
        yield app, db, companies, staff, platforms


def client_for(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_public_logo_url_does_not_expose_storage_path(platform_app):
    app, db, _, _, platforms = platform_app
    platforms[0].logo_path = "internal-storage/synthetic-logo.png"
    db.commit()
    async with client_for(app) as client:
        response = await client.get(
            "/v1/platforms/info",
            headers={"X-Platform-API-Key": platforms[0].api_key},
        )
    assert response.status_code == 200
    assert response.json().get("logo_url", "").endswith(
        f"/v1/platforms/{platforms[0].id}/logo"
    )
    assert "internal-storage" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["website", "wecom", "email", "telegram", "slack", "custom"]
)
async def test_visitor_info_never_serializes_administrator_configuration(
    platform_app, kind
):
    app, db, _, _, platforms = platform_app
    own = platforms[0]
    own.type = kind
    db.commit()
    async with client_for(app) as client:
        response = await client.get(
            "/v1/platforms/info", headers={"X-Platform-API-Key": own.api_key}
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) <= {
        "id",
        "name",
        "display_name",
        "type",
        "is_active",
        "service_available",
        "logo_url",
        "config",
    }
    assert "synthetic-private-value" not in response.text
    assert type(body["service_available"]) is bool
    assert "synthetic-nested-secret" not in response.text
    assert own.api_key not in response.text
    if kind == "website":
        assert body["config"] == {
            "widget_title": "域见客服",
            "theme_color": "#123456",
            "display_mode": "big",
        }
    else:
        assert body["config"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["project_deleted", "platform_deleted", "disabled", "unknown_key"]
)
async def test_visitor_info_rejects_inactive_company_or_channel(
    platform_app, state
):
    app, db, companies, _, platforms = platform_app
    key = platforms[0].api_key
    if state == "project_deleted":
        companies[0].deleted_at = datetime.now(timezone.utc)
    elif state == "platform_deleted":
        platforms[0].deleted_at = datetime.now(timezone.utc)
    elif state == "disabled":
        platforms[0].is_active = False
    else:
        key = "unknown-key"
    db.commit()
    async with client_for(app) as client:
        response = await client.get(
            "/v1/platforms/info", headers={"X-Platform-API-Key": key}
        )
    assert response.status_code in (401, 403)
    assert "synthetic-private-value" not in response.text


@pytest.mark.asyncio
async def test_malformed_presentation_value_cannot_smuggle_nested_credentials(
    platform_app,
):
    app, db, _, _, platforms = platform_app
    platforms[0].config = {
        "widget_title": {"secret": "nested-private"},
        "position": "invalid",
        "welcome_message": "您好",
        "display_mode": 5,
    }
    db.commit()
    async with client_for(app) as client:
        response = await client.get(
            "/v1/platforms/info",
            headers={"X-Platform-API-Key": platforms[0].api_key},
        )
    assert response.status_code == 200
    assert response.json()["config"] == {"welcome_message": "您好"}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("GET", "", None),
        ("PATCH", "", {"name": "stolen"}),
        ("DELETE", "", None),
        ("POST", "/regenerate_api_key", None),
        ("POST", "/enable", None),
        ("POST", "/disable", None),
        ("POST", "/enable-ai", None),
        ("POST", "/disable-ai", None),
    ],
)
async def test_foreign_channel_denied_even_with_resource_permission(
    platform_app, role, method, suffix, body
):
    app, db, _, staff, platforms = platform_app
    staff.role = role
    db.commit()
    foreign = platforms[1]
    before = (
        foreign.name,
        foreign.api_key,
        foreign.is_active,
        foreign.deleted_at,
        foreign.ai_mode,
    )
    token = create_access_token(staff.username, staff.project_id, role)
    async with client_for(app) as client:
        response = await client.request(
            method,
            f"/v1/platforms/{foreign.id}{suffix}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
    assert response.status_code == 404, response.text
    db.refresh(foreign)
    assert before == (
        foreign.name,
        foreign.api_key,
        foreign.is_active,
        foreign.deleted_at,
        foreign.ai_mode,
    )
    assert foreign.api_key not in response.text


@pytest.mark.asyncio
async def test_scoped_list_and_admin_detail_keep_separate_fields(
    platform_app,
):
    app, _, _, staff, platforms = platform_app
    token = create_access_token(staff.username, staff.project_id, staff.role)
    async with client_for(app) as client:
        headers = {"Authorization": f"Bearer {token}"}
        listed = await client.get("/v1/platforms", headers=headers)
        detail = await client.get(
            f"/v1/platforms/{platforms[0].id}", headers=headers
        )
    assert listed.status_code == detail.status_code == 200
    assert [row["id"] for row in listed.json()["data"]] == [
        str(platforms[0].id)
    ]
    assert "synthetic-private-value" not in listed.text
    assert detail.json()["config"]["app_secret"] == "synthetic-private-value"
