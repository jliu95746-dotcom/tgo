"""Merchant contracts are normalized without weakening customer ownership."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.domain.business_tools.http_provider import (
    HTTPBusinessProvider,
    HTTPBusinessProviderConfig,
)
from app.domain.business_tools.models import (
    BusinessQueryContext,
    LogisticsQueryInput,
    OrderQueryInput,
)
from app.domain.business_tools.service import (
    BusinessQueryAccessDenied,
    BusinessQueryProviderError,
    BusinessQueryService,
    BusinessQueryTimeout,
)
from app.schemas.business_connections import BusinessHTTPConfiguration
from app.domain.business_tools.merchant_time import normalize_merchant_time


class Audit:
    def __init__(self):
        self.events = []

    async def record(self, event):
        self.events.append(event)


def context():
    return BusinessQueryContext(
        tenant_id=uuid4(),
        visitor_id="verified-visitor",
        external_customer_id="member-001",
        conversation_id="conversation-001",
        request_id="request-001",
        actor_id="agent-001",
    )


def configuration(**changes):
    return BusinessHTTPConfiguration(
        base_url="https://merchant.example.test",
        merchant_id="shop-42",
        request_order_no_field="orderNumber",
        request_customer_id_field="memberId",
        request_merchant_id_field="shopId",
        request_operation_field="action",
        order_operation_value="lookupOrder",
        logistics_operation_value="lookupShipment",
        data_path="result",
        success_field="code",
        success_value="OK",
        not_found_values=["MISSING"],
        tenant_id_field="shopId",
        customer_id_field="memberId",
        order_no_field="orderNumber",
        order_status_field="state",
        logistics_status_field="state",
        order_status_map={"SENT": "已发货"},
        logistics_status_map={"TRANSIT": "运输中"},
        logistics_not_shipped_values=["PENDING"],
        default_timezone="Asia/Shanghai",
        **changes,
    )


def service(config, handler):
    audit = Audit()
    provider = HTTPBusinessProvider(
        HTTPBusinessProviderConfig(**config.model_dump()),
        transport=httpx.MockTransport(handler),
    )
    return BusinessQueryService(
        provider,
        audit,
        timeout_seconds=1,
        audit_fingerprint_key=b"merchant-mapping-test-key-only-0001",
    ), audit


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_real_merchant_request_status_amount_and_timezone(method):
    config = configuration(method=method)
    caller = context()

    def response(request):
        body = (
            dict(request.url.params) if method == "GET" else json.loads(request.content)
        )
        assert body == {
            "orderNumber": "ORDER-001",
            "memberId": "member-001",
            "shopId": "shop-42",
            "action": "lookupOrder",
        }
        assert request.headers["X-TGO-Tenant-ID"] == str(caller.tenant_id)
        return httpx.Response(
            200,
            json={
                "code": "OK",
                "result": {
                    "shopId": "shop-42",
                    "memberId": "member-001",
                    "orderNumber": "ORDER-001",
                    "state": "SENT",
                    "amount_minor": 29900,
                    "currency": "CNY",
                    "created_at": "2026-10-12T12:00:00",
                },
            },
        )

    query, audit = service(config, response)
    result = await query.query_order(OrderQueryInput(order_no="ORDER-001"), caller)
    assert result.status == "已发货" and result.amount_minor == 29900
    assert result.created_at == datetime(2026, 10, 12, 4, tzinfo=UTC)
    assert len(audit.events) == 1 and audit.events[0].outcome == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"shopId": "other-shop"},
        {"memberId": "other-member"},
        {"orderNumber": "ORDER-OTHER"},
    ],
)
async def test_mapping_still_rejects_foreign_ownership(changed):
    data = {
        "shopId": "shop-42",
        "memberId": "member-001",
        "orderNumber": "ORDER-001",
        "state": "SENT",
    } | changed
    query, audit = service(
        configuration(),
        lambda request: httpx.Response(200, json={"code": "OK", "result": data}),
    )
    with pytest.raises(BusinessQueryAccessDenied):
        await query.query_order(OrderQueryInput(order_no="ORDER-001"), context())
    assert audit.events[0].outcome == "denied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,expected", [("MISSING", "not_found"), ("UPSTREAM_ERROR", "business_failed")]
)
async def test_business_codes_have_safe_distinct_outcomes(code, expected):
    query, audit = service(
        configuration(),
        lambda request: httpx.Response(
            200, json={"code": code, "secret": "upstream-private-value"}
        ),
    )
    with pytest.raises(BusinessQueryProviderError) as error:
        await query.query_order(OrderQueryInput(order_no="ORDER-001"), context())
    assert getattr(error.value, "code", None) == expected
    assert "upstream-private-value" not in str(error.value)
    assert audit.events[0].outcome == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_not_shipped_requires_owned_order_before_classification(foreign):
    data = {
        "shopId": "shop-42",
        "memberId": "other-member" if foreign else "member-001",
        "orderNumber": "ORDER-001",
        "state": "PENDING",
    }
    query, audit = service(
        configuration(),
        lambda request: httpx.Response(200, json={"code": "OK", "result": data}),
    )
    error_type = BusinessQueryAccessDenied if foreign else BusinessQueryProviderError
    with pytest.raises(error_type) as error:
        await query.query_logistics(
            LogisticsQueryInput(order_no="ORDER-001"), context()
        )
    if not foreign:
        assert getattr(error.value, "code", None) == "not_shipped"
    assert audit.events[0].outcome == ("denied" if foreign else "failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_authentication_failure_is_classified_without_response_details(status):
    query, audit = service(
        configuration(),
        lambda request: httpx.Response(
            status, json={"secret": "upstream-private-value"}
        ),
    )
    with pytest.raises(BusinessQueryProviderError) as error:
        await query.query_order(OrderQueryInput(order_no="ORDER-001"), context())
    assert getattr(error.value, "code", None) == "authentication_failed"
    assert (
        "upstream-private-value" not in str(error.value)
        and audit.events[0].outcome == "failed"
    )


@pytest.mark.asyncio
async def test_http_timeout_is_a_timeout_instead_of_generic_failure():
    def response(request):
        raise httpx.ReadTimeout("private-order-number", request=request)

    query, audit = service(configuration(), response)
    with pytest.raises(BusinessQueryTimeout):
        await query.query_order(OrderQueryInput(order_no="ORDER-001"), context())
    assert audit.events[0].outcome == "timeout"


@pytest.mark.asyncio
async def test_configured_status_dictionary_rejects_unknown_status():
    data = {
        "shopId": "shop-42",
        "memberId": "member-001",
        "orderNumber": "ORDER-001",
        "state": "UNKNOWN_PRIVATE_TEXT",
    }
    query, audit = service(
        configuration(),
        lambda request: httpx.Response(200, json={"code": "OK", "result": data}),
    )
    with pytest.raises(BusinessQueryProviderError) as error:
        await query.query_order(OrderQueryInput(order_no="ORDER-001"), context())
    assert (
        "UNKNOWN_PRIVATE_TEXT" not in str(error.value)
        and audit.events[0].outcome == "failed"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"request_customer_id_field": "order_no"},
        {"request_merchant_id_field": "bad.name"},
        {"request_operation_field": "__proto__"},
        {"not_found_values": ["0"], "success_field": "code"},
        {"not_found_values": ["MISSING"]},
        {
            "logistics_status_map": {"PENDING": "运输中"},
            "logistics_not_shipped_values": ["PENDING"],
        },
        {"order_status_map": {"SENT": "bad\nstatus"}},
        {"default_timezone": "Invalid/Timezone"},
    ],
)
def test_ambiguous_or_unsafe_mapping_cannot_be_saved(changes):
    with pytest.raises(ValidationError):
        BusinessHTTPConfiguration(base_url="https://merchant.example.test", **changes)


@pytest.mark.parametrize("time", ["2026-03-08T02:30:00", "2026-11-01T01:30:00"])
def test_nonexistent_or_ambiguous_merchant_time_requires_explicit_offset(time):
    with pytest.raises(ValueError):
        normalize_merchant_time(time, "America/New_York")
    assert normalize_merchant_time(
        "2026-11-01T01:30:00-04:00", "America/New_York"
    ) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_logistics_operation_can_omit_operation_field_and_masks_tracking():
    config = configuration().model_copy(update={"request_operation_field": None})

    def response(request):
        body = json.loads(request.content)
        assert "action" not in body and "operation" not in body
        return httpx.Response(
            200,
            json={
                "code": "OK",
                "result": {
                    "shopId": "shop-42",
                    "memberId": "member-001",
                    "orderNumber": "ORDER-001",
                    "state": "TRANSIT",
                    "carrier": "快递",
                    "tracking_no": "SF12345678",
                },
            },
        )

    query, audit = service(config, response)
    result = await query.query_logistics(
        LogisticsQueryInput(order_no="ORDER-001"), context()
    )
    assert result.status == "运输中" and result.tracking_no_masked != "SF12345678"
    assert audit.events[0].outcome == "success"


@pytest.mark.asyncio
async def test_missing_customer_evidence_and_boolean_success_are_not_confused():
    config = configuration().model_copy(update={"success_value": "True"})
    data = {"shopId": "shop-42", "orderNumber": "ORDER-001", "state": "SENT"}
    query, audit = service(
        config, lambda request: httpx.Response(200, json={"code": True, "result": data})
    )
    with pytest.raises(BusinessQueryProviderError):
        await query.query_order(OrderQueryInput(order_no="ORDER-001"), context())
    assert audit.events[0].outcome == "failed"
