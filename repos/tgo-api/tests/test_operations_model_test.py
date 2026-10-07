"""Operator connection checks use saved secrets without returning provider bodies."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from app.core.exceptions import TGOAPIException
from app.schemas.shared_models import SharedConnectionRequest
from app.services import operations_model_test
from tests.test_shared_models import _catalogue


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 401, 302])
async def test_connection_response_never_returns_secrets(monkeypatch, status):
    catalogue = _catalogue()
    monkeypatch.setattr(
        operations_model_test, "stored_shared_models", lambda db: catalogue
    )
    remote = httpx.Response(
        status, json={"secret": "synthetic-secret-provider-response"}
    )
    client = AsyncMock()
    client.request.return_value = remote
    factory = Mock(return_value=client)
    client.__aenter__.return_value = client
    monkeypatch.setattr(operations_model_test.httpx, "AsyncClient", factory)
    result = await operations_model_test.test_connection(
        Mock(),
        SharedConnectionRequest(
            provider_id=catalogue.providers[0].id, expected_version=1
        ),
    )
    assert result.success is (status == 200)
    assert result.http_status == status
    assert "synthetic" not in result.model_dump_json()
    assert factory.call_args.kwargs["follow_redirects"] is False
    assert (
        client.request.call_args.kwargs["headers"]["Authorization"]
        == "Bearer synthetic-chat-key"
    )


@pytest.mark.asyncio
async def test_stale_version_and_foreign_provider_cannot_be_tested(monkeypatch):
    catalogue = _catalogue()
    monkeypatch.setattr(
        operations_model_test, "stored_shared_models", lambda db: catalogue
    )
    for payload in (
        SharedConnectionRequest(provider_id=uuid4(), expected_version=1),
        SharedConnectionRequest(
            provider_id=catalogue.providers[0].id, expected_version=2
        ),
    ):
        with pytest.raises(TGOAPIException):
            await operations_model_test.test_connection(Mock(), payload)
