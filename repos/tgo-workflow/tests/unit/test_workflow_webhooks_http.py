"""Private registration routes enforce identity and never cache credentials."""

from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI
import httpx
from pydantic import SecretStr
import pytest
import pytest_asyncio

from app.api import webhooks as routes
from app.config import settings
from app.database import get_db
from tests.unit.test_workflow_webhooks import (
    Fixture,
    request,
    webhook_database,
)


@pytest_asyncio.fixture
async def fixture(monkeypatch) -> AsyncIterator[Fixture]:
    monkeypatch.setattr(
        settings, "SECRET_KEY", SecretStr("synthetic-internal-key")
    )
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr('{"local":"synthetic-local-webhook-master-key-123456"}'),
    )
    monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID", "local")
    async with webhook_database() as value:
        yield value


@pytest_asyncio.fixture
async def client(fixture: Fixture) -> AsyncIterator[httpx.AsyncClient]:
    application = FastAPI()
    application.include_router(routes.router, prefix="/v1/workflows")

    async def session():
        async with fixture.sessions() as db:
            yield db

    application.dependency_overrides[get_db] = session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as value:
        yield value


def internal_request(fixture, revision=0):
    payload = request(fixture, revision=revision).model_dump(mode="json")
    payload.update(
        project_id=str(fixture.project_id),
        actor_staff_id=str(fixture.actor_id),
    )
    return payload


def headers():
    return {"X-Internal-API-Key": "synthetic-internal-key"}


@pytest.mark.asyncio
async def test_every_private_route_rejects_missing_identity(client, fixture):
    root = f"/v1/workflows/{fixture.workflow_id}/webhook"
    command = dict(
        request_id=str(uuid4()),
        expected_revision=1,
        project_id=str(fixture.project_id),
        actor_staff_id=str(fixture.actor_id),
    )
    for method, suffix, payload in [
        ("GET", "", None),
        ("PUT", "", internal_request(fixture)),
        ("POST", "/disable", command),
        ("POST", "/rotate", command),
        ("GET", "/commands/" + command["request_id"], None),
    ]:
        result = await client.request(
            method,
            root + suffix,
            json=payload,
            params={
                "project_id": str(fixture.project_id),
                "actor_staff_id": str(fixture.actor_id),
            },
        )
        assert result.status_code == 403


@pytest.mark.asyncio
async def test_save_and_recovery_return_current_credential_with_no_store(
    client, fixture
):
    root = f"/v1/workflows/{fixture.workflow_id}/webhook"
    payload = internal_request(fixture)
    first = await client.put(root, json=payload, headers=headers())
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    secret = first.json()["credential"]["secret"]
    assert len(secret) >= 32 and "*" not in secret
    recovered = await client.get(
        root + "/commands/" + payload["request_id"],
        headers=headers(),
        params={
            "project_id": str(fixture.project_id),
            "actor_staff_id": str(fixture.actor_id),
        },
    )
    assert recovered.status_code == 200
    assert recovered.json()["credential"]["secret"] == secret
    state = await client.get(
        root, headers=headers(), params={"project_id": str(fixture.project_id)}
    )
    assert state.status_code == 200 and secret not in state.text
    assert "credential_ciphertext" not in state.text
    assert state.headers["cache-control"] == "no-store"
    hidden = await client.get(
        root + "/commands/" + payload["request_id"],
        headers=headers(),
        params={
            "project_id": str(fixture.project_id),
            "actor_staff_id": str(uuid4()),
        },
    )
    assert hidden.status_code == 404 and secret not in hidden.text


@pytest.mark.asyncio
async def test_wrong_key_and_missing_configuration_do_not_leak_values(
    client, fixture, monkeypatch
):
    root = f"/v1/workflows/{fixture.workflow_id}/webhook"
    query = {"project_id": str(fixture.project_id)}
    wrong = await client.get(
        root, params=query, headers={"X-Internal-API-Key": "untrusted-key"}
    )
    assert wrong.status_code == 403 and "untrusted-key" not in wrong.text
    monkeypatch.setattr(settings, "SECRET_KEY", None)
    missing = await client.get(root, params=query, headers=headers())
    assert missing.status_code == 503


@pytest.mark.asyncio
async def test_header_accepts_utf8_safely_without_comparison_type_errors(
    client, fixture, monkeypatch
):
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("合成内部密钥"))
    denied = await client.get(
        f"/v1/workflows/{fixture.workflow_id}/webhook",
        params={"project_id": str(fixture.project_id)},
        headers=headers(),
    )
    assert denied.status_code == 403
