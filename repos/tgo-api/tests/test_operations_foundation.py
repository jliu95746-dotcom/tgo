"""Platform identity and migration previews must not grant tenant authority."""

from datetime import datetime, timezone
from secrets import token_urlsafe

import httpx
import pytest
from fastapi import Depends, FastAPI
from jose import jwt
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import operations
from app.core import security
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.models import Project, Staff
from app.models.platform_operator import PlatformOperator
from app.services import operations_auth
from app.schemas.operations import OperatorCreateRequest


@pytest.fixture(scope="module")
def operator_password():
    password = token_urlsafe(24)
    return password, security.get_password_hash(password)


@pytest.fixture
def operations_app(monkeypatch, operator_password):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "OPS_ENABLED", True)
    monkeypatch.setattr(
        settings, "OPS_SECRET_KEY", SecretStr(token_urlsafe(40))
    )

    async def no_limit(*args):
        return None

    monkeypatch.setattr(operations, "limit_operator_login", no_limit)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (Project, Staff, PlatformOperator):
        model.__table__.create(engine)
    with Session(engine) as db:
        company = Project(name="企业 A", api_key="never-return-project-key")
        db.add(company)
        db.flush()
        staff = Staff(
            username="operator@example.com",
            project_id=company.id,
            password_hash=operator_password[1],
            role="admin",
        )
        operator = PlatformOperator(
            email="operator@example.com",
            name="域见运营",
            password_hash=operator_password[1],
        )
        db.add_all([staff, operator])
        db.commit()
        app = FastAPI()
        app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
        app.include_router(operations.router, prefix="/ops")
        app.dependency_overrides[get_db] = lambda: db

        @app.get("/tenant")
        def tenant(context=Depends(security.get_authenticated_project)):
            return {"project_id": str(context[0].id)}

        yield app, db, operator, staff, company, operator_password[0]
    engine.dispose()


async def login(client, password):
    return await client.post(
        "/ops/login",
        json={
            "email": "OPERATOR@example.com",
            "password": password,
        },
    )


@pytest.mark.asyncio
async def test_operator_can_preview_without_mutation_or_secrets(
    operations_app,
):
    app, db, operator, staff, company, password = operations_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        result = await login(client, password)
        assert result.status_code == 200, result.text
        token = result.json()["access_token"]
        assert "password" not in result.text
        headers = {"Authorization": f"Bearer {token}"}
        profile = await client.get("/ops/me", headers=headers)
        assert profile.status_code == 200
        assert profile.json()["id"] == str(operator.id)
        preview = await client.get("/ops/migration-preview", headers=headers)
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert data["will_change_data"] is False
        assert data["pagination"]["total"] == 1
        assert data["data"][0]["human_accounts"] == 1
        assert data["data"][0]["administrator_accounts"] == 1
        assert data["data"][0]["action"] == "review_required"
        for secret in (
            company.api_key,
            staff.password_hash,
            operator.password_hash,
        ):
            assert secret not in preview.text
        tenant = await client.get("/tenant", headers=headers)
        assert tenant.status_code == 401
    db.refresh(company)
    assert company.name == "企业 A" and company.deleted_at is None
    assert db.query(Project).count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/ops/me", "/ops/migration-preview"])
async def test_tenant_admin_cannot_enter_operations(operations_app, path):
    app, _, _, staff, company, _ = operations_app
    token = security.create_access_token(
        staff.username, company.id, role="admin"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            path, headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition", ["disabled", "revoked", "deleted", "expired"]
)
async def test_operator_token_rechecks_current_account(
    operations_app, condition
):
    app, db, operator, _, _, password = operations_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        result = await login(client, password)
        token = result.json()["access_token"]
        if condition == "disabled":
            operator.is_active = False
        elif condition == "revoked":
            operator.token_version += 1
        elif condition == "deleted":
            operator.deleted_at = datetime.now(timezone.utc)
        else:
            payload = jwt.decode(
                token,
                settings.OPS_SECRET_KEY.get_secret_value(),
                algorithms=["HS256"],
                audience="yujian-operations",
            )
            payload["exp"] = 1
            token = jwt.encode(
                payload,
                settings.OPS_SECRET_KEY.get_secret_value(),
                algorithm="HS256",
            )
        db.commit()
        response = await client.get(
            "/ops/me", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_disabled_feature_does_not_expose_operations(
    operations_app, monkeypatch
):
    app, _, _, _, _, password = operations_app
    monkeypatch.setattr(settings, "OPS_ENABLED", False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        assert (await login(client, password)).status_code == 404
        assert (await client.get("/ops/status")).json()["enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("saas_enabled", [False, True])
@pytest.mark.parametrize("ops_enabled", [False, True])
async def test_operations_rollout_is_independent(
    operations_app,
    monkeypatch,
    saas_enabled,
    ops_enabled,
):
    app, _, _, _, _, password = operations_app
    monkeypatch.setattr(settings, "SAAS_ENABLED", saas_enabled)
    monkeypatch.setattr(settings, "SAAS_NEW_PURCHASES_ENABLED", False)
    monkeypatch.setattr(settings, "OPS_ENABLED", ops_enabled)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        status = (await client.get("/ops/status")).json()
        assert status == {
            "enabled": ops_enabled,
            "login_available": ops_enabled,
        }
        response = await login(client, password)
        assert response.status_code == (200 if ops_enabled else 404)
        if ops_enabled:
            headers = {
                "Authorization": f"Bearer {response.json()['access_token']}",
            }
            assert (
                await client.get("/ops/me", headers=headers)
            ).status_code == 200
            monkeypatch.setattr(settings, "OPS_ENABLED", False)
            assert (
                await client.get("/ops/me", headers=headers)
            ).status_code == 404
    assert settings.SAAS_ENABLED is saas_enabled
    assert settings.SAAS_NEW_PURCHASES_ENABLED is False


@pytest.mark.asyncio
async def test_missing_key_and_wrong_password_fail_closed(
    operations_app, monkeypatch
):
    app, _, _, _, _, password = operations_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        assert (await login(client, "wrong-password")).status_code == 401
        monkeypatch.setattr(settings, "OPS_SECRET_KEY", None)
        assert (await login(client, password)).status_code == 503


@pytest.mark.asyncio
async def test_preview_pagination_excludes_deleted_and_ai_accounts(
    operations_app,
):
    app, db, _, _, company, password = operations_app
    db.add(
        Staff(
            username="ai-account",
            project_id=company.id,
            password_hash="unused",
            role="agent",
        )
    )
    db.add(
        Project(
            name="Deleted",
            api_key="deleted-key",
            deleted_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        token = (await login(client, password)).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        result = (
            await client.get("/ops/migration-preview?limit=1", headers=headers)
        ).json()
        assert result["pagination"]["total"] == 1
        assert result["data"][0]["human_accounts"] == 1
        assert (
            await client.get(
                "/ops/migration-preview?limit=101", headers=headers
            )
        ).status_code == 422
        result = (
            await client.get(
                "/ops/migration-preview?offset=1", headers=headers
            )
        ).json()
        assert result["data"] == []


def test_local_provisioning_hashes_password_and_preserves_staff(
    operations_app,
):
    _, db, _, staff, _, password = operations_app
    count = db.query(Staff).count()
    data = OperatorCreateRequest(
        email="NEW@example.com", name="新运营人员", password=password
    )
    created = operations_auth.create_operator(db, data)
    operator = db.query(PlatformOperator).filter_by(id=created.id).one()
    assert operator.email == "new@example.com"
    assert operator.password_hash != password
    assert security.verify_password(password, operator.password_hash)
    assert db.query(Staff).count() == count
    assert staff.role == "admin"
    assert "password" not in created.model_dump_json()
    with pytest.raises(TGOAPIException) as error:
        operations_auth.create_operator(db, data)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_operator_logout_revokes_existing_sessions(operations_app):
    app, _, _, _, _, password = operations_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        token = (await login(client, password)).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert (
            await client.post("/ops/logout", headers=headers)
        ).status_code == 204
        assert (
            await client.get("/ops/me", headers=headers)
        ).status_code == 401
