"""Webhook management never trusts a browser-supplied actor or stale admin."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import workflow_webhooks as routes
from app.core.database import Base, get_db
from app.core.security import get_current_active_user
from app.models.project import Project
from app.models.staff import Staff
from app.schemas.workflow_webhook import WebhookReceipt, WebhookState


@dataclass
class Harness:
    client: httpx.AsyncClient
    db: Session
    staff: Staff
    project: Project


@pytest_asyncio.fixture
async def harness() -> AsyncIterator[Harness]:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(
        engine, tables=[Project.__table__, Staff.__table__]
    )
    with Session(engine, expire_on_commit=False) as db:
        project = Project(id=uuid4(), name="Webhook 合成企业", api_key=uuid4().hex)
        db.add(project)
        db.flush()
        staff = Staff(
            id=uuid4(),
            project_id=project.id,
            username="webhook-test-" + uuid4().hex[:12],
            password_hash="disabled-test-login",
            role="admin",
            account_enabled=True,
            token_version=1,
        )
        db.add(staff)
        db.commit()
        application = FastAPI()
        application.include_router(routes.router, prefix="/v1/ai/workflows")
        application.dependency_overrides[get_db] = lambda: db
        application.dependency_overrides[
            get_current_active_user
        ] = lambda: staff
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            yield Harness(client, db, staff, project)
    engine.dispose()


def payload():
    return dict(
        request_id=str(uuid4()),
        expected_revision=0,
        expected_release_id=str(uuid4()),
        expected_release_digest="a" * 64,
        enabled=False,
    )


def receipt(harness, body, operation="save"):
    now = datetime.now(timezone.utc).isoformat()
    return WebhookReceipt.model_validate(
        dict(
            request_id=body["request_id"],
            operation=operation,
            replayed=False,
            credential_status="available",
            credential={
                "secret": "synthetic-credential-for-proxy-tests-only-123456",
            },
            webhook=dict(
                id=str(uuid4()),
                project_id=str(harness.project.id),
                workflow_id="owned-flow",
                revision=1,
                credential_revision=1,
                key_id="sender-v1",
                enabled=False,
                release_id=body.get("expected_release_id", "release"),
                release_digest="a" * 64,
                actor_staff_id=str(harness.staff.id),
                created_by=str(harness.staff.id),
                updated_by=str(harness.staff.id),
                created_at=now,
                updated_at=now,
            ),
        )
    )


@pytest.mark.asyncio
async def test_authenticated_identity_and_no_store(harness, monkeypatch):
    body = payload()
    upstream = AsyncMock(return_value=receipt(harness, body))
    monkeypatch.setattr(routes.workflow_client, "save_webhook", upstream)
    result = await harness.client.put(
        "/v1/ai/workflows/owned-flow/webhook",
        json=body,
        params={"project_id": str(uuid4()), "actor_staff_id": str(uuid4())},
    )
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    args = upstream.await_args.args
    assert args[:3] == ("owned-flow", harness.project.id, harness.staff.id)
    assert args[3].model_dump(mode="json") == body
    assert result.json()["credential"]["secret"].startswith("synthetic-")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "injected", ["project_id", "actor_staff_id", "secret", "key_id"]
)
async def test_browser_cannot_inject_identity_or_credentials(
    harness, monkeypatch, injected
):
    upstream = AsyncMock()
    monkeypatch.setattr(routes.workflow_client, "save_webhook", upstream)
    body = payload()
    body[injected] = str(uuid4())
    result = await harness.client.put(
        "/v1/ai/workflows/owned-flow/webhook", json=body
    )
    assert result.status_code == 422
    upstream.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["role", "account_enabled", "token_version"]
)
async def test_admin_revocation_during_upstream_wait_hides_credential(
    harness,
    monkeypatch,
    change,
):
    body = payload()
    original = receipt(harness, body)

    async def revoke(*args):
        setattr(
            harness.staff,
            change,
            {"role": "user", "account_enabled": False, "token_version": 2}[
                change
            ],
        )
        harness.db.commit()
        return original

    monkeypatch.setattr(
        routes.workflow_client, "save_webhook", AsyncMock(side_effect=revoke)
    )
    result = await harness.client.put(
        "/v1/ai/workflows/owned-flow/webhook", json=body
    )
    assert result.status_code == 403
    assert original.credential.secret.get_secret_value() not in result.text


@pytest.mark.asyncio
async def test_non_admin_cannot_read_or_mutate(harness, monkeypatch):
    upstream = AsyncMock()
    monkeypatch.setattr(routes.workflow_client, "get_webhook", upstream)
    monkeypatch.setattr(routes.workflow_client, "save_webhook", upstream)
    monkeypatch.setattr(routes.workflow_client, "change_webhook", upstream)
    monkeypatch.setattr(
        routes.workflow_client, "get_webhook_command", upstream
    )
    harness.staff.role = "user"
    harness.db.commit()
    root = "/v1/ai/workflows/owned-flow/webhook"
    for method, suffix, body in [
        ("GET", "", None),
        ("PUT", "", payload()),
        (
            "POST",
            "/disable",
            {"request_id": str(uuid4()), "expected_revision": 1},
        ),
        (
            "POST",
            "/rotate",
            {"request_id": str(uuid4()), "expected_revision": 1},
        ),
        ("GET", "/commands/" + str(uuid4()), None),
    ]:
        result = await harness.client.request(method, root + suffix, json=body)
        assert result.status_code == 403
    upstream.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["disable", "rotate"])
async def test_change_and_command_recovery_use_original_admin(
    harness, monkeypatch, operation
):
    body = {"request_id": str(uuid4()), "expected_revision": 1}
    expected = receipt(harness, body, operation)
    upstream = AsyncMock(return_value=expected)
    monkeypatch.setattr(routes.workflow_client, "change_webhook", upstream)
    result = await harness.client.post(
        "/v1/ai/workflows/owned-flow/webhook/" + operation, json=body
    )
    assert result.status_code == 200
    assert upstream.await_args.args[:4] == (
        "owned-flow",
        harness.project.id,
        harness.staff.id,
        operation,
    )
    recovery = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        routes.workflow_client, "get_webhook_command", recovery
    )
    result = await harness.client.get(
        "/v1/ai/workflows/owned-flow/webhook/commands/" + body["request_id"]
    )
    assert result.status_code == 200
    assert recovery.await_args.args[1:3] == (
        harness.project.id,
        harness.staff.id,
    )


@pytest.mark.asyncio
async def test_state_read_uses_authenticated_project(harness, monkeypatch):
    upstream = AsyncMock(
        return_value=WebhookState(
            webhook=None, binding_current=False, registration_available=False
        )
    )
    monkeypatch.setattr(routes.workflow_client, "get_webhook", upstream)
    result = await harness.client.get("/v1/ai/workflows/owned-flow/webhook")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    upstream.assert_awaited_once_with("owned-flow", harness.project.id)
