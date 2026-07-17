"""Contract tests for the isolated read-only business query core."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

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
