"""The model inventory is never a public or tenant-admin endpoint."""

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from starlette.requests import Request

from app.api.v1.model_usage import require_usage_reader
from app.config import settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "received,allowed",
    [("", False), ("customer-token", False), ("synthetic-internal-test-token", True)],
)
async def test_only_trusted_service_can_read_usage(monkeypatch, received, allowed):
    monkeypatch.setattr(settings, "saas_enabled", True)
    monkeypatch.setattr(settings, "saas_billing_enabled", True)
    monkeypatch.setattr(
        settings, "saas_internal_token", SecretStr("synthetic-internal-test-token")
    )
    request = Request(
        {"type": "http", "headers": [(b"x-saas-service-token", received.encode())]}
    )
    if allowed:
        await require_usage_reader(request)
    else:
        with pytest.raises(HTTPException) as error:
            await require_usage_reader(request)
        assert error.value.status_code == 403
