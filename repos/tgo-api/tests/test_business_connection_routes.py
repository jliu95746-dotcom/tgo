"""Public administration and private credential resolution stay separate."""

import base64
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.business_connections import router
from app.api.internal.endpoints.business_connections import router as private_router
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import (
    TGOAPIException,
    tgo_api_exception_handler,
    validation_exception_handler,
)
from app.core.security import get_current_active_user
from app.models.business_connection import BusinessConnection
from app.models.staff import Staff


@pytest.fixture
def fixture(monkeypatch):
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_CREDENTIAL_KEY",
        SecretStr(base64.urlsafe_b64encode(os.urandom(32)).decode()),
    )
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_SERVICE_TOKEN",
        SecretStr("private-fixture-business-token-32-characters"),
    )
    monkeypatch.setattr(settings, "SAAS_ENABLED", False)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Staff.__table__.create(engine)
    BusinessConnection.__table__.create(engine)
    with Session(engine) as db:
        actor = Staff(
            id=uuid4(),
            project_id=uuid4(),
            username="connection-admin",
            password_hash="not-an-authenticating-hash",
            role="admin",
        )
        db.add(actor)
        db.commit()
        app = FastAPI()
        app.include_router(router, prefix="/v1/business-connections")
        app.include_router(private_router, prefix="/internal/business-connections")
        app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
        app.add_exception_handler(RequestValidationError, validation_exception_handler)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_active_user] = lambda: actor
        yield app, db, actor
    engine.dispose()


def payload():
    return {
        "request_id": str(uuid4()),
        "name": "澜月订单连接",
        "mode": "http",
        "configuration": {
            "base_url": "https://orders.example.test",
            "auth_mode": "bearer",
        },
        "credentials": {"token": "fixture-private-route-credential"},
    }


@pytest.mark.asyncio
async def test_public_crud_uses_current_tenant_and_never_returns_saved_credentials(
    fixture,
):
    app, db, actor = fixture
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = payload()
        created = await client.post("/v1/business-connections", json=body)
        assert created.status_code == 201
        path = "/v1/business-connections/" + created.json()["id"]
        assert "fixture-private-route-credential" not in created.text
        assert (await client.post("/v1/business-connections", json=body)).json()[
            "id"
        ] == created.json()["id"]
        for response in [
            await client.get(path),
            await client.get("/v1/business-connections"),
        ]:
            assert (
                response.status_code == 200
                and "credential_ciphertext" not in response.text
                and "fixture-private-route-credential" not in response.text
            )
        renamed = await client.patch(
            path, json={"expected_revision": 1, "name": "订单连接"}
        )
        assert (
            renamed.status_code == 200 and renamed.json()["configuration_revision"] == 1
        )
        assert (
            await client.patch(path, json={"expected_revision": 1, "name": "旧编辑"})
        ).status_code == 409
        assert (
            await client.patch(path, json={"expected_revision": 2, "enabled": True})
        ).status_code == 409
        assert (
            await client.post(path + "/disable", json={"expected_revision": 2})
        ).status_code == 200
        app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
            id=actor.id, project_id=uuid4(), role="admin", username="foreign"
        )
        assert (await client.get(path)).status_code == 404
        assert (await client.get("/v1/business-connections")).json() == []
        assert (
            await client.patch(path, json={"expected_revision": 2, "name": "foreign"})
        ).status_code == 404


@pytest.mark.asyncio
async def test_non_admin_and_forged_owner_cannot_configure_or_export(fixture):
    app, db, actor = fixture
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=actor.id, project_id=actor.project_id, role="user", username="ordinary"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post("/v1/business-connections", json=payload())
        ).status_code == 403
        assert (await client.get("/v1/business-connections")).status_code == 403
        app.dependency_overrides[get_current_active_user] = lambda: actor
        forged = {
            **payload(),
            "project_id": str(uuid4()),
            "verified_capabilities": ["order_query"],
        }
        assert (
            await client.post("/v1/business-connections", json=forged)
        ).status_code == 422
    assert db.query(BusinessConnection).count() == 0


@pytest.mark.asyncio
async def test_invalid_credentials_are_redacted_from_response_and_validation_logs(
    fixture, caplog
):
    app, _, _ = fixture
    body = payload()
    body["configuration"]["base_url"] = (
        "https://user:validation-sensitive-fixture@orders.example.test"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/business-connections", json=body)
    assert response.status_code == 422
    assert "validation-sensitive-fixture" not in response.text + caplog.text
    assert "fixture-private-route-credential" not in response.text + caplog.text


@pytest.mark.asyncio
async def test_private_resolution_requires_distinct_service_token_exact_version_and_current_admin(
    fixture,
):
    app, db, actor = fixture
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        saved = (await client.post("/v1/business-connections", json=payload())).json()
        path = "/internal/business-connections/" + saved["id"] + "/resolve"
        body = {
            "project_id": str(actor.project_id),
            "actor_id": str(actor.id),
            "expected_revision": 1,
            "purpose": "configuration_test",
        }
        assert (await client.post(path, json=body)).status_code == 403
        assert (
            await client.post(
                path,
                json=body,
                headers={"X-Business-Service-Token": settings.SECRET_KEY},
            )
        ).status_code == 403
        headers = {
            "X-Business-Service-Token": settings.BUSINESS_CONNECTION_SERVICE_TOKEN.get_secret_value()
        }
        response = await client.post(path, json=body, headers=headers)
        assert (
            response.status_code == 200
            and response.json()["credentials"]["token"]
            == "fixture-private-route-credential"
        )
        assert (
            response.json()["can_enable"] is False
            and response.json()["scope"] == "business_connection_configuration"
        )
        assert (
            await client.post(
                path, json={**body, "expected_revision": 2}, headers=headers
            )
        ).status_code == 409
        assert (
            await client.post(
                path, json={**body, "project_id": str(uuid4())}, headers=headers
            )
        ).status_code == 403
        assert (
            await client.post(
                path, json={**body, "purpose": "execute"}, headers=headers
            )
        ).status_code == 422
        actor.account_enabled = False
        db.commit()
        assert (await client.post(path, json=body, headers=headers)).status_code == 403
        actor.account_enabled = True
        actor.role = "user"
        db.commit()
        assert (await client.post(path, json=body, headers=headers)).status_code == 403
