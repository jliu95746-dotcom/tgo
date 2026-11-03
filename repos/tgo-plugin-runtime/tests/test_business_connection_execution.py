"""Configuration tests remain scoped, audited and distinct from live execution."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.config import settings
from app.domain.business_tools.connection_runner import ConnectionTestRunner
from app.domain.business_tools.demo_provider import DemoHandbagBusinessProvider
from app.domain.business_tools.models import BusinessQueryAuditEvent
from app.domain.business_tools.target_transport import PinnedBusinessTransport
from app.schemas.business_connections import (
    BusinessConnectionCredentials,
    BusinessConnectionPrivateSnapshot,
    BusinessConnectionResponse,
    BusinessHTTPConfiguration,
)
from app.schemas.connection_test_protocol import ConnectionTestCommand
from app.services.business_connection_client import (
    BusinessConnectionOwnerClient,
    ConnectionAuthorityUnavailable,
)


def command():
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
        customer_id="customer-test-only",
    )


def snapshot(query, *, mode="demo"):
    return BusinessConnectionPrivateSnapshot(
        scope="business_connection_configuration",
        can_enable=False,
        connection=BusinessConnectionResponse(
            id=query.connection_id,
            project_id=query.project_id,
            name="测试连接",
            mode=mode,
            capabilities=["order_query", "logistics_query"],
            configuration=BusinessHTTPConfiguration(
                base_url="https://orders.example.test", auth_mode="bearer"
            )
            if mode == "http"
            else None,
            credentials_configured=mode == "http",
            enabled=False,
            revision=1,
            configuration_revision=1,
            authorization_revision=1,
            resource_revision="a" * 64,
            test_status="not_tested",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        credentials=BusinessConnectionCredentials(
            token=SecretStr("test-only-upstream-credential")
        )
        if mode == "http"
        else None,
    )


@pytest.fixture(autouse=True)
def private_settings(monkeypatch):
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_SERVICE_TOKEN",
        SecretStr("test-only-private-business-token-32-characters"),
    )
    monkeypatch.setattr(settings, "BUSINESS_CONNECTION_API_URL", "http://owner.test")


class Audit:
    def __init__(self):
        self.events: list[BusinessQueryAuditEvent] = []

    async def record(self, event):
        self.events.append(event)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["customer_profile_query", "membership_query"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_customer_connection_test_reads_saved_mapping_without_order_number(
    operation, foreign, monkeypatch
):
    query = command().model_copy(update={"operation": operation, "order_no": None})
    saved, audit = snapshot(query, mode="http"), Audit()
    saved.connection.capabilities = [operation]
    saved.connection.configuration = BusinessHTTPConfiguration(
        base_url="https://orders.example.test",
        customer_profile_path="/v1/customers/profile/query",
        membership_path="/v1/memberships/query",
    )
    owner = AsyncMock()
    owner.resolve.return_value = saved

    def respond(request):
        assert request.url.path == (
            "/v1/customers/profile/query"
            if operation == "customer_profile_query"
            else "/v1/memberships/query"
        )
        body = json.loads(request.content)
        assert body == {"tenant_id": str(query.project_id), "customer_id": query.customer_id}
        return httpx.Response(200, json={"data": {
            "tenant_id": str(query.project_id),
            "customer_id": "another-customer" if foreign else query.customer_id,
            "segment": "常客", "service_tags": ["关注上新"],
            "tier": "银卡", "points_balance": 120,
        }})

    monkeypatch.setattr(
        "app.domain.business_tools.customer_tools.PinnedBusinessTransport",
        lambda origin, paths: httpx.MockTransport(respond),
    )
    receipt = await ConnectionTestRunner(owner, audit=audit).run(query)
    assert receipt.outcome == ("failed" if foreign else "passed")
    assert receipt.can_enable is False
    assert (receipt.summary is None) is foreign
    if foreign:
        assert receipt.error_code == "denied"
    else:
        assert receipt.summary is not None and receipt.summary.status == "查询成功"
    assert query.customer_id not in receipt.model_dump_json()
    assert len(audit.events) == 1 and audit.events[0].operation == operation
    assert owner.resolve.await_count == 2


def test_customer_test_command_rejects_order_number_and_order_test_requires_it():
    from pydantic import ValidationError

    base = command().model_dump()
    with pytest.raises(ValidationError):
        ConnectionTestCommand.model_validate({**base, "order_no": None})
    with pytest.raises(ValidationError):
        ConnectionTestCommand.model_validate({**base, "operation": "membership_query"})


@pytest.mark.asyncio
async def test_owner_client_requests_exact_private_purpose_and_keeps_credentials_private():
    query = command()
    saved = snapshot(query, mode="http")
    calls = []

    def respond(request):
        calls.append(request)
        assert (
            request.url.path
            == f"/internal/business-connections/{query.connection_id}/resolve"
        )
        assert json.loads(request.content) == {
            "project_id": str(query.project_id),
            "actor_id": str(query.actor_id),
            "expected_revision": 1,
            "purpose": "configuration_test",
        }
        assert request.headers["X-Business-Service-Token"].startswith(
            "test-only-private"
        )
        return httpx.Response(200, content=saved.model_dump_json())

    result = await BusinessConnectionOwnerClient(
        transport=httpx.MockTransport(respond)
    ).resolve(query)
    assert (
        result.credentials.token.get_secret_value() == "test-only-upstream-credential"
    )
    assert "test-only-upstream-credential" not in repr(result)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("id", str(uuid4())),
        ("project_id", str(uuid4())),
        ("revision", 2),
        ("configuration_revision", 2),
        ("authorization_revision", 2),
        ("resource_revision", "b" * 64),
        ("capabilities", ["logistics_query"]),
    ],
)
async def test_owner_rejects_foreign_stale_or_wrong_operation_snapshot(field, value):
    query = command()
    body = snapshot(query).model_dump(mode="json")
    body["connection"][field] = value
    with pytest.raises(ConnectionAuthorityUnavailable):
        await BusinessConnectionOwnerClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=body)
            )
        ).resolve(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["scope", "can_enable", "credentials"])
async def test_owner_rejects_omitted_private_authority_fields(field):
    query = command()
    body = snapshot(query).model_dump(mode="json")
    del body[field]
    with pytest.raises(ConnectionAuthorityUnavailable):
        await BusinessConnectionOwnerClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=body)
            )
        ).resolve(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 404, 409, 500])
async def test_owner_failure_never_surfaces_private_response_body(status):
    query = command()
    with pytest.raises(ConnectionAuthorityUnavailable) as error:
        await BusinessConnectionOwnerClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, text="private fixture secret")
            )
        ).resolve(query)
    assert "private fixture secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["order_query", "logistics_query"])
async def test_demo_is_audited_but_cannot_become_real_pass(operation):
    query = command().model_copy(update={"operation": operation})
    saved, audit = snapshot(query), Audit()
    owner = AsyncMock()
    owner.resolve.return_value = saved
    result = await ConnectionTestRunner(owner, audit=audit).run(query)
    assert (
        result.matches(query)
        and result.outcome == "demo"
        and result.can_enable is False
    )
    assert owner.resolve.await_count == 2 and len(audit.events) == 1
    assert query.order_no not in result.model_dump_json()
    assert query.customer_id not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, SecretStr("short")])
async def test_runner_fails_safely_before_provider_when_private_configuration_missing(
    token, monkeypatch
):
    query = command()
    owner = AsyncMock()
    owner.resolve.return_value = snapshot(query)
    monkeypatch.setattr(settings, "BUSINESS_CONNECTION_SERVICE_TOKEN", token)
    runner = ConnectionTestRunner(owner, audit=Audit())
    monkeypatch.setattr(
        runner, "_provider", lambda saved: pytest.fail("provider must not be built")
    )
    with pytest.raises(ConnectionAuthorityUnavailable):
        await runner.run(query)


@pytest.mark.asyncio
async def test_authority_change_during_query_cannot_return_old_result():
    query = command()
    saved = snapshot(query)
    changed = saved.model_copy(
        update={
            "connection": saved.connection.model_copy(
                update={"authorization_revision": 2}
            )
        }
    )
    owner, audit = AsyncMock(), Audit()
    owner.resolve.side_effect = [saved, changed]
    with pytest.raises(ConnectionAuthorityUnavailable):
        await ConnectionTestRunner(owner, audit=audit).run(query)
    assert len(audit.events) == 1


@pytest.mark.asyncio
async def test_audit_failure_returns_failure_without_provider_details():
    query = command()
    owner, audit = AsyncMock(), AsyncMock()
    owner.resolve.return_value = snapshot(query)
    audit.record.side_effect = RuntimeError("private database failure")
    receipt = await ConnectionTestRunner(owner, audit=audit).run(query)
    assert receipt.outcome == "failed" and receipt.error_code == "audit_unavailable"
    assert (
        receipt.summary is None
        and "private database failure" not in receipt.model_dump_json()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,foreign,status",
    [
        ("order_query", False, 200),
        ("logistics_query", False, 200),
        ("order_query", True, 200),
        ("order_query", False, 401),
    ],
)
async def test_saved_http_configuration_uses_pinned_query_and_checks_ownership(
    operation, foreign, status, monkeypatch, caplog
):
    query = command().model_copy(update={"operation": operation})
    saved, audit = snapshot(query, mode="http"), Audit()
    owner = AsyncMock()
    owner.resolve.return_value = saved
    seen = []

    def respond(request):
        seen.append(request)
        assert (
            request.url.host == "1.1.1.1"
            and request.headers["Host"] == "orders.example.test"
        )
        assert request.extensions["sni_hostname"] == "orders.example.test"
        assert (
            request.headers["Authorization"] == "Bearer test-only-upstream-credential"
        )
        payload = json.loads(request.content)
        assert payload["customer_id"] == query.customer_id
        return httpx.Response(
            status,
            json={
                "data": {
                    "tenant_id": str(query.project_id),
                    "customer_id": "other" if foreign else query.customer_id,
                    "order_no": query.order_no,
                    "status": "shipped",
                    "amount_minor": 29900,
                    "currency": "CNY",
                    "carrier": "示例快递",
                    "tracking_no": "SF12345678",
                }
            },
        )

    async def resolve(host, port):
        return ["1.1.1.1"]

    monkeypatch.setattr(
        "app.domain.business_tools.connection_runner.PinnedBusinessTransport",
        lambda origin, paths: PinnedBusinessTransport(
            origin, paths, resolver=resolve, inner=httpx.MockTransport(respond)
        ),
    )
    with caplog.at_level("INFO", logger="httpx"):
        receipt = await ConnectionTestRunner(owner, audit=audit).run(query)
    expected = "failed" if foreign or status != 200 else "passed"
    assert receipt.outcome == expected and len(seen) == len(audit.events) == 1
    if foreign:
        assert receipt.error_code == "denied"
    if operation == "logistics_query" and receipt.summary:
        assert receipt.summary.tracking_no_masked == "SF****5678"
    assert query.order_no not in caplog.text and query.customer_id not in caplog.text
    assert "test-only-upstream-credential" not in caplog.text


@pytest.mark.asyncio
async def test_external_cancellation_is_not_converted_to_test_success(monkeypatch):
    import asyncio

    query = command()
    owner, audit = AsyncMock(), Audit()
    owner.resolve.return_value = snapshot(query)

    class CancelProvider(DemoHandbagBusinessProvider):
        async def query_order(self, query, context):
            raise asyncio.CancelledError

    runner = ConnectionTestRunner(owner, audit=audit)
    monkeypatch.setattr(runner, "_provider", lambda saved: CancelProvider())
    with pytest.raises(asyncio.CancelledError):
        await runner.run(query)
    assert owner.resolve.await_count == 1 and not audit.events


@pytest.mark.asyncio
async def test_provider_echo_of_order_or_customer_is_removed_from_persistent_summary(
    monkeypatch,
):
    query = command()
    owner = AsyncMock()
    owner.resolve.return_value = snapshot(query)

    class EchoProvider(DemoHandbagBusinessProvider):
        async def query_order(self, request, context):
            value = await super().query_order(request, context)
            return value.model_copy(
                update={
                    "result": value.result.model_copy(
                        update={
                            "status": "已发货 "
                            + request.order_no
                            + " "
                            + context.external_customer_id,
                        }
                    )
                }
            )

    runner = ConnectionTestRunner(owner, audit=Audit())
    monkeypatch.setattr(runner, "_provider", lambda saved: EchoProvider())
    result = await runner.run(query)
    assert query.order_no not in result.model_dump_json()
    assert query.customer_id not in result.model_dump_json()
    assert result.summary.status.startswith("已发货")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,code,status,expected",
    [
        ("order_query", "MISSING", 200, "not_found"),
        ("order_query", "BAD", 200, "business_failed"),
        ("order_query", "OK", 401, "authentication_failed"),
        ("logistics_query", "OK", 200, "not_shipped"),
    ],
)
async def test_runner_persists_classified_mapping_failures_without_pass_or_private_data(
    operation, code, status, expected, monkeypatch
):
    query = command().model_copy(update={"operation": operation})
    saved, audit = snapshot(query, mode="http"), Audit()
    saved.connection.configuration = BusinessHTTPConfiguration(
        base_url="https://orders.example.test",
        merchant_id="shop-42",
        success_field="code",
        success_value="OK",
        not_found_values=["MISSING"],
        logistics_not_shipped_values=["PENDING"],
    )
    owner = AsyncMock()
    owner.resolve.return_value = saved

    def response(request):
        assert json.loads(request.content)["tenant_id"] == "shop-42"
        return httpx.Response(
            status,
            json={
                "code": code,
                "data": {
                    "tenant_id": "shop-42",
                    "customer_id": query.customer_id,
                    "order_no": query.order_no,
                    "status": "PENDING",
                    "upstream_private": "private response detail",
                },
            },
        )

    monkeypatch.setattr(
        "app.domain.business_tools.connection_runner.PinnedBusinessTransport",
        lambda origin, paths: httpx.MockTransport(response),
    )
    receipt = await ConnectionTestRunner(owner, audit=audit).run(query)
    assert receipt.error_code == expected and receipt.outcome == "failed"
    assert receipt.summary is None and receipt.can_enable is False
    assert (
        query.customer_id not in receipt.model_dump_json()
        and query.order_no not in receipt.model_dump_json()
    )
    assert "private response detail" not in receipt.model_dump_json()
    assert len(audit.events) == 1 and audit.events[0].outcome == "failed"
