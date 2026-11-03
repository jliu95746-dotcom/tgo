"""Customer and membership reads must be owned, bounded and audited."""

from uuid import uuid4
import json

import httpx
import pytest
from pydantic import ValidationError

from app.domain.business_tools.customer_tools import (
    CustomerHTTPConfig,
    CustomerHTTPProvider,
    CustomerProfileResult,
    CustomerQueryService,
    MembershipResult,
)
from app.domain.business_tools.http_provider import HTTPBusinessProviderConfig
from app.domain.business_tools.models import (
    BusinessQueryAuditEvent,
    BusinessQueryContext,
)
from app.domain.business_tools.service import (
    BusinessQueryAccessDenied,
    BusinessQueryAuditError,
    BusinessQueryProviderError,
)
from app.schemas.business_connections import BusinessHTTPConfiguration


def context(*, customer_id: str | None = "customer-123") -> BusinessQueryContext:
    return BusinessQueryContext(
        tenant_id=uuid4(),
        visitor_id="visitor-123",
        external_customer_id=customer_id,
        conversation_id="conversation-123",
        request_id="request-123",
        actor_id="agent-123",
    )


class Audit:
    def __init__(self, fail: bool = False) -> None:
        self.events: list[BusinessQueryAuditEvent] = []
        self.fail = fail

    async def record(self, event: BusinessQueryAuditEvent) -> None:
        if self.fail:
            raise RuntimeError("private database error")
        self.events.append(event)


def provider(
    requested_context: BusinessQueryContext,
    *,
    customer_id: str = "customer-123",
    tenant_id: str | None = None,
) -> tuple[CustomerHTTPProvider, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "tenant_id": tenant_id or str(requested_context.tenant_id),
                    "customer_id": customer_id,
                    "tier": "金卡",
                    "points_balance": 120,
                    "segment": "常客",
                    "service_tags": ["偏好手提包"],
                    "phone": "13800000000",
                    "email": "private@example.test",
                }
            },
        )

    config = CustomerHTTPConfig(
        base_url="https://business.example.test",
        profile_path="/v1/customers/profile/query",
        membership_path="/v1/memberships/query",
    )
    return (
        CustomerHTTPProvider(config, transport=httpx.MockTransport(handler)),
        captured,
    )


@pytest.mark.asyncio
async def test_membership_uses_trusted_customer_id_and_audits_without_pii() -> None:
    caller = context()
    upstream, captured = provider(caller)
    audit = Audit()
    service = CustomerQueryService(
        upstream,
        audit,
        timeout_seconds=1,
        audit_fingerprint_key=b"test-key-32-bytes-long-for-audit!!",
    )

    result = await service.query_membership(caller)

    assert result == MembershipResult(tier="金卡", points_balance=120)
    assert len(captured) == 1
    assert captured[0].url.path == "/v1/memberships/query"
    assert captured[0].method == "POST"
    assert b"customer-123" in captured[0].content
    assert audit.events[0].operation == "membership_query"
    assert audit.events[0].outcome == "success"
    assert "customer-123" not in audit.events[0].model_dump_json()
    assert "13800000000" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_profile_only_returns_allowlisted_non_contact_fields() -> None:
    caller = context()
    upstream, captured = provider(caller)
    result = await CustomerQueryService(
        upstream,
        Audit(),
        timeout_seconds=1,
        audit_fingerprint_key=b"test-key-32-bytes-long-for-audit!!",
    ).query_profile(caller)

    assert result == CustomerProfileResult(segment="常客", service_tags=["偏好手提包"])
    assert captured[0].url.path == "/v1/customers/profile/query"
    assert "phone" not in result.model_dump_json()
    assert "email" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["customer", "tenant"])
async def test_mismatched_ownership_never_releases_customer_data(other: str) -> None:
    caller = context()
    upstream, _ = provider(
        caller,
        customer_id="someone-else" if other == "customer" else "customer-123",
        tenant_id=str(uuid4()) if other == "tenant" else None,
    )
    audit = Audit()
    service = CustomerQueryService(
        upstream,
        audit,
        timeout_seconds=1,
        audit_fingerprint_key=b"test-key-32-bytes-long-for-audit!!",
    )

    with pytest.raises(BusinessQueryAccessDenied):
        await service.query_membership(caller)

    assert audit.events[0].outcome == "denied"


@pytest.mark.asyncio
async def test_unbound_customer_and_failed_audit_fail_closed() -> None:
    caller = context(customer_id=None)
    upstream, captured = provider(caller)
    service = CustomerQueryService(
        upstream,
        Audit(),
        timeout_seconds=1,
        audit_fingerprint_key=b"test-key-32-bytes-long-for-audit!!",
    )
    with pytest.raises(BusinessQueryAccessDenied):
        await service.query_profile(caller)
    assert captured == []

    caller = context()
    upstream, _ = provider(caller)
    service = CustomerQueryService(
        upstream,
        Audit(fail=True),
        timeout_seconds=1,
        audit_fingerprint_key=b"test-key-32-bytes-long-for-audit!!",
    )
    with pytest.raises(BusinessQueryAuditError):
        await service.query_profile(caller)


def test_customer_result_contract_rejects_contact_fields_and_unsafe_paths() -> None:
    with pytest.raises(ValidationError):
        CustomerProfileResult(segment="常客", phone="13800000000")
    with pytest.raises(ValidationError):
        MembershipResult(tier="金卡", points_balance=-1)
    with pytest.raises(ValidationError):
        CustomerProfileResult(segment="13800000000")
    with pytest.raises(ValidationError):
        CustomerProfileResult(service_tags=["private@example.test"])
    with pytest.raises(ValueError):
        CustomerHTTPConfig(
            base_url="https://business.example.test",
            profile_path="//evil.example.test/steal",
            membership_path="/v1/memberships/query",
        )


@pytest.mark.asyncio
async def test_saved_project_connection_mapping_drives_customer_queries() -> None:
    caller = context()
    saved = BusinessHTTPConfiguration(
        base_url="https://merchant.example.test",
        merchant_id="shop-42",
        request_customer_id_field="memberId",
        request_merchant_id_field="shopId",
        tenant_id_field="shopId",
        customer_id_field="memberId",
        data_path="result",
        customer_profile_path="/v1/customers/profile/query",
        membership_path="/v1/memberships/query",
        customer_segment_field="segmentLabel",
        customer_tags_field="serviceTags",
        membership_tier_field="membership.level",
        membership_points_field="membership.points",
    )
    # The old order/logistics adapter must still accept saved connection data.
    HTTPBusinessProviderConfig(**saved.model_dump())
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "result": {
                    "shopId": "shop-42",
                    "memberId": "customer-123",
                    "segmentLabel": "常客",
                    "serviceTags": ["偏好手提包"],
                    "membership": {"level": "金卡", "points": 120},
                }
            },
        )

    upstream = CustomerHTTPProvider(
        CustomerHTTPConfig.from_business_connection(saved),
        transport=httpx.MockTransport(handler),
    )
    audit = Audit()
    service = CustomerQueryService(
        upstream,
        audit,
        timeout_seconds=1,
        audit_fingerprint_key=b"test-key-32-bytes-long-for-audit!!",
    )
    assert (await service.query_profile(caller)).segment == "常客"
    assert (await service.query_membership(caller)).points_balance == 120
    assert all(
        json.loads(request.content) == {"shopId": "shop-42", "memberId": "customer-123"}
        for request in captured
    )
    assert {event.operation for event in audit.events} == {
        "customer_profile_query",
        "membership_query",
    }


def test_customer_connection_paths_must_be_a_safe_complete_pair() -> None:
    base = {"base_url": "https://merchant.example.test"}
    with pytest.raises(ValidationError):
        BusinessHTTPConfiguration(
            **base, customer_profile_path="/v1/customers/profile/query"
        )
    with pytest.raises(ValidationError):
        BusinessHTTPConfiguration(
            **base,
            customer_profile_path="/v1/customers/profile/query",
            membership_path="//evil.example.test/steal",
        )


def test_customer_query_has_no_direct_route_before_formal_authorization() -> None:
    from app.main import app

    assert "/business/customer/query" not in {
        route.path for route in app.routes if hasattr(route, "path")
    }
    with pytest.raises(ValueError):
        CustomerHTTPConfig(
            base_url="https://business.example.test",
            profile_path="/v1/customers/profile/query",
            membership_path="/v1/memberships/query",
            auth_mode="api_key",
            auth_token="test-key",
            api_key_header="X-Api-Key\r\nInjected",
        )


@pytest.mark.asyncio
async def test_upstream_business_failure_is_audited_without_result() -> None:
    caller = context()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "BUSINESS_FAILED", "data": {"tier": "金卡"}},
        )

    upstream = CustomerHTTPProvider(
        CustomerHTTPConfig(
            base_url="https://business.example.test",
            profile_path="/v1/customers/profile/query",
            membership_path="/v1/memberships/query",
            success_field="code",
            success_value="0",
        ),
        transport=httpx.MockTransport(handler),
    )
    audit = Audit()
    service = CustomerQueryService(
        upstream,
        audit,
        timeout_seconds=1,
        audit_fingerprint_key=b"test-key-32-bytes-long-for-audit!!",
    )

    with pytest.raises(BusinessQueryProviderError):
        await service.query_membership(caller)
    assert audit.events[0].outcome == "failed"
