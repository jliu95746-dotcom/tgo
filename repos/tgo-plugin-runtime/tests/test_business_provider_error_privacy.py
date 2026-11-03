"""Failed read-only merchant requests must not expose request identities in errors."""

from __future__ import annotations

import traceback
from uuid import uuid4

import httpx
import pytest

from app.domain.business_tools.customer_tools import (
    CustomerHTTPConfig,
    CustomerHTTPProvider,
)
from app.domain.business_tools.http_provider import (
    HTTPBusinessProvider,
    HTTPBusinessProviderConfig,
)
from app.domain.business_tools.models import BusinessQueryContext, OrderQueryInput
from app.domain.business_tools.service import (
    BusinessQueryProviderError,
    BusinessQueryService,
)

PRIVATE_ORDER = "PRIVATE-ORDER-123"
PRIVATE_CUSTOMER = "private-customer-marker"


def caller() -> BusinessQueryContext:
    return BusinessQueryContext(
        tenant_id=uuid4(),
        visitor_id="verified-visitor",
        external_customer_id=PRIVATE_CUSTOMER,
        conversation_id="conversation-1",
        request_id="request-1",
        actor_id="agent-1",
    )


class Audit:
    def __init__(self) -> None:
        self.events = []

    async def record(self, event) -> None:
        self.events.append(event)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("http_status", "transport"))
async def test_order_failure_audits_without_order_or_customer_in_traceback(
    failure: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "transport":
            raise httpx.ConnectError(PRIVATE_CUSTOMER, request=request)
        return httpx.Response(503)

    provider = HTTPBusinessProvider(
        HTTPBusinessProviderConfig(
            base_url="https://merchant.example.test",
            order_path="/order",
            logistics_path="/logistics",
            method="GET",
        ),
        transport=httpx.MockTransport(handler),
    )
    audit = Audit()
    service = BusinessQueryService(
        provider,
        audit,
        timeout_seconds=1,
        audit_fingerprint_key=b"fixture-audit-key-at-least-32-bytes",
    )

    with pytest.raises(BusinessQueryProviderError) as caught:
        await service.query_order(OrderQueryInput(order_no=PRIVATE_ORDER), caller())

    trace = "".join(traceback.format_exception(caught.value))
    assert PRIVATE_ORDER not in trace
    assert PRIVATE_CUSTOMER not in trace
    assert len(audit.events) == 1
    assert audit.events[0].outcome == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("http_status", "transport"))
async def test_customer_provider_error_does_not_keep_private_path_or_message(
    failure: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "transport":
            raise httpx.ConnectError(PRIVATE_CUSTOMER, request=request)
        return httpx.Response(503)

    provider = CustomerHTTPProvider(
        CustomerHTTPConfig(
            base_url="https://merchant.example.test",
            profile_path=f"/{PRIVATE_CUSTOMER}",
            membership_path="/membership",
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(Exception) as caught:
        await provider.query_profile(caller())

    trace = "".join(traceback.format_exception(caught.value))
    assert PRIVATE_CUSTOMER not in trace
    assert caught.value.__context__ is None
