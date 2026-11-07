"""Provider boundaries for read-only business systems."""

from __future__ import annotations

from typing import Literal, Protocol

from app.domain.business_tools.models import (
    BusinessQueryAuditEvent,
    BusinessQueryContext,
    LogisticsQueryInput,
    OrderQueryInput,
    OwnedLogisticsQueryResult,
    OwnedOrderQueryResult,
)


class BusinessProviderAccessDenied(ValueError):
    """The upstream response did not prove customer ownership."""


BusinessResultCode = Literal[
    "not_found", "not_shipped", "authentication_failed", "business_failed"
]


class BusinessProviderResultError(ValueError):
    """A classified upstream outcome with no raw data or exception text."""

    def __init__(self, code: BusinessResultCode) -> None:
        self.code = code
        super().__init__(code)


class ReadOnlyBusinessProvider(Protocol):
    (
        "A provider that exposes queries and deliberately has no mutation meth"
        "ods."
    )

    async def query_order(
        self,
        query: OrderQueryInput,
        context: BusinessQueryContext,
    ) -> OwnedOrderQueryResult:
        """Return a tenant-scoped order plus ownership evidence."""
        ...

    async def query_logistics(
        self,
        query: LogisticsQueryInput,
        context: BusinessQueryContext,
    ) -> OwnedLogisticsQueryResult:
        """Return tenant-scoped logistics plus ownership evidence."""
        ...


class BusinessQueryAuditSink(Protocol):
    (
        "Required audit boundary; implementations persist PII-minimized events"
        "."
    )

    async def record(self, event: BusinessQueryAuditEvent) -> None:
        """Persist an audit event before query data is returned."""
        ...
