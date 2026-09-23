"""A missing or unavailable quota authority never starts a paid model call."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from starlette.requests import Request

from app.services import quota_authorization as quota


def request(token="synthetic-token"):
    return Request(
        {"type": "http", "headers": [(b"x-saas-service-token", token.encode())]}
    )


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(quota.settings, "saas_enabled", True)
    monkeypatch.setattr(quota.settings, "saas_billing_enabled", True)
    monkeypatch.setattr(
        quota.settings, "saas_internal_token", SecretStr("synthetic-token")
    )


@pytest.mark.asyncio
async def test_untrusted_caller_is_rejected_before_http(monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", send)
    with pytest.raises(HTTPException) as error:
        await quota.authorize(request("wrong"), uuid4())
    assert error.value.status_code == 403
    send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [402, 403, 503])
async def test_quota_failure_is_not_treated_as_authorization(monkeypatch, status):
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://synthetic.invalid")
    )
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))
    with pytest.raises(HTTPException) as error:
        await quota.authorize(request(), uuid4())
    assert error.value.status_code == status


@pytest.mark.asyncio
async def test_success_requires_explicit_authorization(monkeypatch):
    response = httpx.Response(
        200,
        json={"authorized": True, "metered": True},
        request=httpx.Request("POST", "https://synthetic.invalid"),
    )
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))
    await quota.authorize(request(), uuid4())
