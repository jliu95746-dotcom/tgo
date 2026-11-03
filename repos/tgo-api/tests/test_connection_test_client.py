"""Private adapter receipts must identify the original test without leaking inputs."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.connection_test_protocol import (
    ConnectionTestCommand,
    ConnectionTestReceipt,
)
from app.services.connection_test_client import request_connection_test


def query():
    return ConnectionTestCommand(
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


def receipt(command):
    return ConnectionTestReceipt(
        scope="business_connection_test",
        can_enable=False,
        **command.model_dump(exclude={"order_no", "customer_id"}),
        mode="http",
        outcome="failed",
        summary=None,
        error_code="denied",
        checked_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def private_token(monkeypatch):
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_SERVICE_TOKEN",
        SecretStr("test-only-private-business-token-32-characters"),
    )


@pytest.mark.asyncio
async def test_typed_client_uses_distinct_token_and_exact_receipt():
    command = query()

    def respond(request):
        assert request.url.path == "/business/connections/test"
        assert request.headers["X-Business-Service-Token"].startswith(
            "test-only-private"
        )
        assert ConnectionTestCommand.model_validate_json(request.content) == command
        return httpx.Response(200, content=receipt(command).model_dump_json())

    result = await request_connection_test(
        "http://adapter.test", 60, command, transport=httpx.MockTransport(respond)
    )
    assert result.matches(command)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"request_id": str(uuid4())},
        {"actor_id": str(uuid4())},
        {"scope": "employee_activation"},
        {"can_enable": True},
        {"mode": "demo", "outcome": "passed"},
        {"checked_at": "1990-01-01T00:00:00Z"},
    ],
)
async def test_invalid_foreign_or_stale_receipt_is_never_accepted(change):
    command = query()
    body = receipt(command).model_dump(mode="json") | change
    with pytest.raises(TGOAPIException) as error:
        await request_connection_test(
            "http://adapter.test",
            60,
            command,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=body)
            ),
        )
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [302, 401, 409, 500])
async def test_error_body_not_exposed_in_exception_or_log(code, caplog):
    with pytest.raises(TGOAPIException) as error:
        await request_connection_test(
            "http://adapter.test",
            60,
            query(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(code, text="do-not-leak-fixture-secret")
            ),
        )
    assert "do-not-leak-fixture-secret" not in str(error.value) + caplog.text


@pytest.mark.asyncio
async def test_missing_private_token_cannot_use_shared_key(monkeypatch):
    monkeypatch.setattr(settings, "BUSINESS_CONNECTION_SERVICE_TOKEN", None)
    with pytest.raises(TGOAPIException):
        await request_connection_test(
            "http://adapter.test",
            60,
            query(),
            transport=httpx.MockTransport(lambda request: pytest.fail("must not send")),
        )
