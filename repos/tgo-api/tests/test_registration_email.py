"""A new company must prove email ownership before creating its project."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi import HTTPException

from app.api.v1.endpoints import registration
from app.schemas.registration import RegistrationRequest
from app.services import registration_email


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def setex(self, key, _seconds, value):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)

    async def eval(self, _script, _keys, key, expected):
        if self.values.get(key) == expected:
            self.values.pop(key)
            return 1
        return 0


@pytest.mark.asyncio
async def test_registration_requires_a_code_in_saas_mode(monkeypatch):
    app = FastAPI()
    app.include_router(registration.router, prefix="/staff")
    monkeypatch.setattr(
        registration.settings, "PUBLIC_REGISTRATION_ENABLED", True
    )
    monkeypatch.setattr(registration.settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(registration, "limit_registration", AsyncMock())
    create = AsyncMock()
    monkeypatch.setattr(registration, "register_project_account", create)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/staff/register",
            json={"username": "owner@example.com", "password": "password-123"},
        )

    assert response.status_code == 422
    create.assert_not_awaited()


def test_registration_code_is_six_digits():
    with pytest.raises(ValueError):
        RegistrationRequest(
            username="owner@example.com",
            password="password-123",
            verification_code="12345",
        )


@pytest.mark.asyncio
async def test_signup_code_is_one_use_and_bound_to_email(monkeypatch):
    store = FakeRedis()
    sent = []
    monkeypatch.setattr(
        registration_email.Redis, "from_url", lambda *_a, **_k: store
    )
    monkeypatch.setattr(
        registration_email.settings, "REDIS_URL", "redis://fixture.invalid/0"
    )
    monkeypatch.setattr(
        registration_email.settings, "SAAS_REGISTRATION_ENABLED", True
    )
    monkeypatch.setattr(
        registration_email.settings, "SAAS_BILLING_ENABLED", True
    )
    monkeypatch.setattr(
        registration_email, "require_mail_configuration", lambda: None
    )
    monkeypatch.setattr(
        registration_email, "runtime_model", lambda _db: object()
    )
    monkeypatch.setattr(registration_email, "limit_registration", AsyncMock())

    async def capture(_send, payload):
        sent.append(payload)

    monkeypatch.setattr(registration_email, "run_in_threadpool", capture)
    db = MagicMock()
    db.scalar.side_effect = [object(), None]
    await registration_email.send_registration_code(db, "Owner@example.com")
    assert len(sent) == 1
    assert sent[0].recipient == "owner@example.com"
    assert "owner@example.com" not in next(iter(store.values.values()))
    import re

    code = re.search(r"(?<!\d)\d{6}(?!\d)", sent[0].body).group()
    with pytest.raises(HTTPException):
        await registration_email.consume_registration_code(
            "other@example.com", code
        )
    await registration_email.consume_registration_code(
        "OWNER@example.com", code
    )
    with pytest.raises(HTTPException):
        await registration_email.consume_registration_code(
            "owner@example.com", code
        )
