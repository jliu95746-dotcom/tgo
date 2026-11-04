"""The adapter test entry rejects public credentials and redacts errors."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from unittest.mock import AsyncMock

from app.api.connection_tests import router, get_connection_test_runner
from app.config import settings
from app.schemas.connection_test_protocol import (
    ConnectionTestCommand,
    ConnectionTestReceipt,
)
from app.services.business_connection_client import ConnectionAuthorityUnavailable


@pytest.fixture
def fixture(monkeypatch):
    token = "fixture-only-distinct-business-token-32-characters"
    monkeypatch.setattr(settings, "BUSINESS_CONNECTION_SERVICE_TOKEN", SecretStr(token))
    app, runner = FastAPI(), AsyncMock()
    app.include_router(router)
    app.dependency_overrides[get_connection_test_runner] = lambda: runner
    query = ConnectionTestCommand(
        project_id=uuid4(),
        actor_id=uuid4(),
        connection_id=uuid4(),
        expected_revision=1,
        configuration_revision=1,
        authorization_revision=1,
        resource_revision="a" * 64,
        request_id=uuid4(),
        operation="order_query",
        order_no="ORDER-TEST-001",
        customer_id="customer-test",
    )
    receipt = ConnectionTestReceipt(
        scope="business_connection_test",
        can_enable=False,
        **query.model_dump(exclude={"order_no", "customer_id"}),
        mode="http",
        outcome="failed",
        summary=None,
        error_code="denied",
        checked_at=datetime.now(UTC),
    )
    runner.run.return_value = receipt
    return app, runner, query, token


@pytest.mark.asyncio
async def test_private_route_accepts_only_dedicated_token_and_exact_command(fixture):
    app, runner, query, token = fixture
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for headers in [
            {},
            {"Authorization": "Bearer unrelated-jwt"},
            {"X-Internal-API-Key": token},
            {"X-Business-Service-Token": "wrong"},
        ]:
            result = await client.post(
                "/business/connections/test",
                json=query.model_dump(mode="json"),
                headers=headers,
            )
            assert result.status_code == 401
        result = await client.post(
            "/business/connections/test",
            json=query.model_dump(mode="json"),
            headers={"X-Business-Service-Token": token},
        )
        assert result.status_code == 200 and result.json()["can_enable"] is False
        assert (
            query.order_no not in result.text and query.customer_id not in result.text
        )
        runner.run.assert_awaited_once_with(query)


@pytest.mark.asyncio
async def test_private_validation_does_not_echo_unknown_credentials_or_input(fixture):
    app, runner, query, token = fixture
    body = {
        **query.model_dump(mode="json"),
        "credentials": {"token": "do-not-echo-fixture-secret"},
        "order_no": "do not echo invalid fixture",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/business/connections/test",
            json=body,
            headers={"X-Business-Service-Token": token},
        )
    assert result.status_code == 422
    assert "do-not-echo" not in result.text and "do not echo" not in result.text
    runner.run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ConnectionAuthorityUnavailable(), RuntimeError("private-fixture-provider-secret")],
)
async def test_private_failure_is_safe_and_cannot_become_pass(fixture, error, caplog):
    app, runner, query, token = fixture
    runner.run.side_effect = error
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/business/connections/test",
            json=query.model_dump(mode="json"),
            headers={"X-Business-Service-Token": token},
        )
    assert result.status_code == 503
    assert "private-fixture-provider-secret" not in result.text + caplog.text


@pytest.mark.asyncio
async def test_mismatched_runner_receipt_is_rejected(fixture):
    app, runner, query, token = fixture
    runner.run.return_value = runner.run.return_value.model_copy(
        update={"request_id": uuid4()}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/business/connections/test",
            json=query.model_dump(mode="json"),
            headers={"X-Business-Service-Token": token},
        )
    assert result.status_code == 503
