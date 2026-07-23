"""Contract tests for the isolated read-only business query core."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.domain.business_tools.http_provider import (
    HTTPBusinessProvider,
    HTTPBusinessProviderConfig,
)
from app.domain.business_tools.audit_sink import DatabaseBusinessQueryAuditSink
from app.domain.business_tools.models import (
    BusinessQueryAuditEvent,
    BusinessQueryContext,
    LogisticsQueryInput,
    LogisticsQueryResult,
    OrderOwnership,
    OrderQueryInput,
    OrderQueryResult,
    OwnedLogisticsQueryResult,
    OwnedOrderQueryResult,
)
from app.domain.business_tools.service import (
    BusinessQueryAccessDenied,
    BusinessQueryAuditError,
    BusinessQueryService,
    BusinessQueryTimeout,
)
from app.api.business_queries import (
    get_business_query_status,
    query_business_data,
)
from app.config import settings
from app.domain.business_tools.models import BusinessQueryRequest
from app.domain.business_tools.models import BusinessQueryContextRequest

TEST_AUDIT_KEY = b"test-only-audit-fingerprint-key-0001"


class FakeAuditSink:
    def __init__(self) -> None:
        self.events: list[BusinessQueryAuditEvent] = []

    async def record(self, event: BusinessQueryAuditEvent) -> None:
        self.events.append(event)


class FailingAuditSink:
    async def record(self, event: BusinessQueryAuditEvent) -> None:
        del event
        raise RuntimeError("database details that must not be surfaced")


class FakeBusinessProvider:
    def __init__(
        self,
        order_result: OwnedOrderQueryResult,
        logistics_result: OwnedLogisticsQueryResult,
        *,
        delay_seconds: float = 0,
    ) -> None:
        self.order_result = order_result
        self.logistics_result = logistics_result
        self.delay_seconds = delay_seconds

    async def query_order(
        self,
        query: OrderQueryInput,
        context: BusinessQueryContext,
    ) -> OwnedOrderQueryResult:
        del query, context
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return self.order_result

    async def query_logistics(
        self,
        query: LogisticsQueryInput,
        context: BusinessQueryContext,
    ) -> OwnedLogisticsQueryResult:
        del query, context
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return self.logistics_result


def make_contract() -> tuple[
    BusinessQueryContext,
    OwnedOrderQueryResult,
    OwnedLogisticsQueryResult,
]:
    tenant_id = uuid4()
    context = BusinessQueryContext(
        tenant_id=tenant_id,
        visitor_id="visitor-001",
        conversation_id="conversation-001",
        request_id="request-001",
        actor_id="customer-service-agent",
    )
    ownership = OrderOwnership(
        tenant_id=tenant_id,
        visitor_id="visitor-001",
        order_no="ORDER-20260716-001",
    )
    order = OwnedOrderQueryResult(
        ownership=ownership,
        result=OrderQueryResult(
            order_no="ORDER-20260716-001",
            status="shipped",
            amount_minor=12900,
            currency="CNY",
            created_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        ),
    )
    logistics = OwnedLogisticsQueryResult(
        ownership=ownership,
        result=LogisticsQueryResult(
            order_no="ORDER-20260716-001",
            status="in_transit",
            carrier="示例快递",
            tracking_no_masked="SF****5678",
            updated_at=datetime(2026, 7, 16, 13, 0, tzinfo=UTC),
        ),
    )
    return context, order, logistics


@pytest.mark.asyncio
async def test_demo_business_query_http_handler_is_tenant_bound() -> None:
    context, _, _ = make_contract()
    previous_mode = settings.BUSINESS_QUERY_MODE
    previous_key = settings.INTERNAL_API_KEY
    settings.BUSINESS_QUERY_MODE = "demo"
    settings.INTERNAL_API_KEY = "test-internal-key"
    try:
        response = await query_business_data(
            BusinessQueryRequest(
                context=BusinessQueryContextRequest.model_validate(
                    context.model_dump()
                ),
                operation="logistics_query",
                order_no="ORDER-20260716-001",
            ),
            x_internal_api_key="test-internal-key",
        )
    finally:
        settings.BUSINESS_QUERY_MODE = previous_mode
        settings.INTERNAL_API_KEY = previous_key

    assert response.logistics is not None
    assert response.logistics.order_no == "ORDER-20260716-001"
    assert response.logistics.tracking_no_masked == "SF****5678"


@pytest.mark.asyncio
async def test_business_query_status_exposes_no_url_or_secret() -> None:
    previous_mode = settings.BUSINESS_QUERY_MODE
    previous_key = settings.INTERNAL_API_KEY
    settings.BUSINESS_QUERY_MODE = "demo"
    settings.INTERNAL_API_KEY = "test-internal-key"
    try:
        response = await get_business_query_status(
            x_internal_api_key="test-internal-key"
        )
    finally:
        settings.BUSINESS_QUERY_MODE = previous_mode
        settings.INTERNAL_API_KEY = previous_key

    assert response.configured is True
    assert response.mode == "demo"
    assert response.auth_mode is None
    serialized = response.model_dump_json()
    assert "test-internal-key" not in serialized
    assert "business-api" not in serialized


def test_query_models_reject_write_operations_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        OrderQueryInput(operation="refund", order_no="ORDER-20260716-001")

    with pytest.raises(ValidationError):
        LogisticsQueryInput(
            operation="query",
            order_no="ORDER-20260716-001",
            change_address="不允许的写入参数",
        )

    with pytest.raises(ValidationError):
        OrderQueryInput(operation="query", order_no="../invalid")


@pytest.mark.asyncio
async def test_order_query_returns_only_an_owned_record_and_audits_success() -> None:
    context, order, logistics = make_contract()
    audit = FakeAuditSink()
    service = BusinessQueryService(
        provider=FakeBusinessProvider(order, logistics),
        audit_sink=audit,
        timeout_seconds=1,
        audit_fingerprint_key=TEST_AUDIT_KEY,
    )

    result = await service.query_order(
        OrderQueryInput(order_no="ORDER-20260716-001"),
        context,
    )

    assert result == order.result
    assert len(audit.events) == 1
    assert audit.events[0].outcome == "success"
    assert audit.events[0].operation == "order_query"
    assert audit.events[0].parameter_fingerprint != "ORDER-20260716-001"
    assert "ORDER-20260716-001" not in audit.events[0].parameter_fingerprint
    assert "visitor-001" not in audit.events[0].visitor_fingerprint


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["tenant", "visitor", "order"])
async def test_order_query_fails_closed_on_any_ownership_mismatch(
    mismatch: str,
) -> None:
    context, order, logistics = make_contract()
    ownership_data = order.ownership.model_dump()
    if mismatch == "tenant":
        ownership_data["tenant_id"] = uuid4()
    elif mismatch == "visitor":
        ownership_data["visitor_id"] = "visitor-other"
    else:
        ownership_data["order_no"] = "ORDER-OTHER-001"
    order = order.model_copy(
        update={"ownership": OrderOwnership(**ownership_data)},
    )
    audit = FakeAuditSink()
    service = BusinessQueryService(
        provider=FakeBusinessProvider(order, logistics),
        audit_sink=audit,
        timeout_seconds=1,
        audit_fingerprint_key=TEST_AUDIT_KEY,
    )

    with pytest.raises(BusinessQueryAccessDenied, match="无法确认订单归属"):
        await service.query_order(
            OrderQueryInput(order_no="ORDER-20260716-001"),
            context,
        )

    assert audit.events[-1].outcome == "denied"


@pytest.mark.asyncio
async def test_logistics_query_checks_ownership_before_returning_data() -> None:
    context, order, logistics = make_contract()
    audit = FakeAuditSink()
    service = BusinessQueryService(
        provider=FakeBusinessProvider(order, logistics),
        audit_sink=audit,
        timeout_seconds=1,
        audit_fingerprint_key=TEST_AUDIT_KEY,
    )

    result = await service.query_logistics(
        LogisticsQueryInput(order_no="ORDER-20260716-001"),
        context,
    )

    assert result == logistics.result
    assert result.tracking_no_masked == "SF****5678"
    assert audit.events[-1].operation == "logistics_query"
    assert audit.events[-1].outcome == "success"


@pytest.mark.asyncio
async def test_provider_timeout_fails_closed_and_is_audited() -> None:
    context, order, logistics = make_contract()
    audit = FakeAuditSink()
    service = BusinessQueryService(
        provider=FakeBusinessProvider(order, logistics, delay_seconds=0.05),
        audit_sink=audit,
        timeout_seconds=0.001,
        audit_fingerprint_key=TEST_AUDIT_KEY,
    )

    with pytest.raises(BusinessQueryTimeout, match="业务查询超时"):
        await service.query_order(
            OrderQueryInput(order_no="ORDER-20260716-001"),
            context,
        )

    assert audit.events[-1].outcome == "timeout"


def test_service_and_provider_contract_expose_no_write_operations() -> None:
    prohibited = {
        "refund",
        "cancel",
        "change_address",
        "change_price",
        "update",
        "delete",
    }

    assert prohibited.isdisjoint(dir(BusinessQueryService))


def test_service_requires_a_nontrivial_audit_fingerprint_key() -> None:
    _, order, logistics = make_contract()

    with pytest.raises(ValueError, match="at least 32 bytes"):
        BusinessQueryService(
            provider=FakeBusinessProvider(order, logistics),
            audit_sink=FakeAuditSink(),
            timeout_seconds=1,
            audit_fingerprint_key=b"too-short",
        )


@pytest.mark.asyncio
async def test_query_fails_closed_when_required_audit_cannot_be_recorded() -> None:
    context, order, logistics = make_contract()
    service = BusinessQueryService(
        provider=FakeBusinessProvider(order, logistics),
        audit_sink=FailingAuditSink(),
        timeout_seconds=1,
        audit_fingerprint_key=TEST_AUDIT_KEY,
    )

    with pytest.raises(BusinessQueryAuditError, match="业务查询审计失败"):
        await service.query_order(
            OrderQueryInput(order_no="ORDER-20260716-001"),
            context,
        )


@pytest.mark.asyncio
async def test_http_provider_authenticates_maps_and_masks_logistics() -> None:
    context, _, _ = make_contract()
    context = context.model_copy(
        update={"external_customer_id": "member-7788"},
    )
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "code": "0",
                "payload": {
                    "project": str(context.tenant_id),
                    "member": "member-7788",
                    "orderId": "ORDER-20260716-001",
                    "delivery": {
                        "state": "运输中",
                        "company": "顺丰速运",
                        "trackingNo": "SF12345678",
                        "updatedAt": "2026-07-16T13:00:00Z",
                    },
                },
            },
        )

    provider = HTTPBusinessProvider(
        HTTPBusinessProviderConfig(
            base_url="https://business.example.test",
            order_path="/v1/orders/query",
            logistics_path="/v1/logistics/query",
            method="POST",
            auth_mode="bearer",
            auth_token="test-token",
            data_path="payload",
            success_field="code",
            success_value="0",
            tenant_id_field="project",
            customer_id_field="member",
            order_no_field="orderId",
            logistics_status_field="delivery.state",
            logistics_carrier_field="delivery.company",
            logistics_tracking_no_field="delivery.trackingNo",
            logistics_updated_at_field="delivery.updatedAt",
        ),
        transport=httpx.MockTransport(handler),
    )

    owned = await provider.query_logistics(
        LogisticsQueryInput(order_no="ORDER-20260716-001"),
        context,
    )

    assert captured_request is not None
    assert captured_request.url == (
        "https://business.example.test/v1/logistics/query"
    )
    assert captured_request.headers["Authorization"] == "Bearer test-token"
    request_body = captured_request.content.decode("utf-8")
    assert "ORDER-20260716-001" in request_body
    assert str(context.tenant_id) in request_body
    assert "member-7788" in request_body
    assert owned.ownership.visitor_id == context.visitor_id
    assert owned.result.tracking_no_masked == "SF****5678"
    assert owned.result.status == "运输中"


@pytest.mark.asyncio
async def test_http_provider_rejects_customer_ownership_mismatch() -> None:
    context, _, _ = make_contract()
    context = context.model_copy(
        update={"external_customer_id": "member-correct"},
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "data": {
                    "tenant_id": str(context.tenant_id),
                    "customer_id": "member-other",
                    "order_no": "ORDER-20260716-001",
                    "status": "已发货",
                }
            },
        )

    provider = HTTPBusinessProvider(
        HTTPBusinessProviderConfig(
            base_url="https://business.example.test",
            order_path="/orders",
            logistics_path="/logistics",
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ValueError, match="customer ownership mismatch"):
        await provider.query_order(
            OrderQueryInput(order_no="ORDER-20260716-001"),
            context,
        )


def test_http_provider_requires_safe_complete_configuration() -> None:
    with pytest.raises(ValueError, match="auth_token"):
        HTTPBusinessProviderConfig(
            base_url="https://business.example.test",
            order_path="/orders",
            logistics_path="/logistics",
            auth_mode="bearer",
        )

    with pytest.raises(ValueError, match="http or https"):
        HTTPBusinessProviderConfig(
            base_url="file:///etc/passwd",
            order_path="/orders",
            logistics_path="/logistics",
        )


@pytest.mark.asyncio
async def test_database_audit_sink_persists_only_fingerprints() -> None:
    context, order, logistics = make_contract()
    audit_event = BusinessQueryAuditEvent(
        tenant_id=context.tenant_id,
        conversation_id=context.conversation_id,
        request_id=context.request_id,
        actor_id=context.actor_id,
        operation="logistics_query",
        outcome="success",
        visitor_fingerprint="a" * 64,
        parameter_fingerprint="b" * 64,
        duration_ms=12.5,
    )

    class FakeSession:
        record: object | None = None
        committed = False

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def add(self, record: object) -> None:
            self.record = record

        async def commit(self) -> None:
            self.committed = True

    session = FakeSession()
    sink = DatabaseBusinessQueryAuditSink(
        session_factory=lambda: session,  # type: ignore[arg-type]
    )

    await sink.record(audit_event)

    assert session.committed is True
    assert session.record is not None
    persisted = vars(session.record)
    assert persisted["visitor_fingerprint"] == "a" * 64
    assert persisted["parameter_fingerprint"] == "b" * 64
    assert "ORDER-20260716-001" not in str(persisted)
    assert context.visitor_id not in str(persisted)
    del order, logistics
