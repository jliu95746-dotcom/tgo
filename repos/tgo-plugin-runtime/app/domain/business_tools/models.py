"""Strict data contracts for read-only order and logistics queries."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$",
    ),
]
OrderNumber = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=4,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
ShortBusinessText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
HexDigest = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
MaskedTrackingNumber = Annotated[
    str,
    StringConstraints(
        min_length=4,
        max_length=40,
        pattern=r"^[A-Za-z0-9-]{0,16}\*{2,}[A-Za-z0-9*-]{0,16}$",
    ),
]


class StrictBusinessModel(BaseModel):
    """Common fail-closed Pydantic configuration for business contracts."""

    model_config = ConfigDict(extra="forbid", strict=True)


class BusinessTransportModel(BaseModel):
    """JSON transport model converted before strict domain validation."""

    model_config = ConfigDict(extra="forbid", strict=False)


class BusinessQueryContext(StrictBusinessModel):
    """Trusted caller identity used to enforce tenant and visitor ownership."""

    tenant_id: UUID
    visitor_id: Identifier
    external_customer_id: Identifier | None = None
    conversation_id: Identifier
    request_id: Identifier
    actor_id: Identifier


class OrderOwnership(StrictBusinessModel):
    """Ownership metadata returned by a tenant-scoped provider query."""

    tenant_id: UUID
    visitor_id: Identifier
    order_no: OrderNumber


class OrderQueryInput(StrictBusinessModel):
    """The only supported order operation is a read-only query."""

    operation: Literal["query"] = "query"
    order_no: OrderNumber


class LogisticsQueryInput(StrictBusinessModel):
    """The only supported logistics operation is a read-only query."""

    operation: Literal["query"] = "query"
    order_no: OrderNumber


class OrderQueryResult(StrictBusinessModel):
    """Safe order fields that may be returned to the AI workflow."""

    order_no: OrderNumber
    status: ShortBusinessText
    amount_minor: int | None = Field(default=None, ge=0)
    currency: (
        Annotated[
            str,
            StringConstraints(pattern=r"^[A-Z]{3}$"),
        ]
        | None
    ) = None
    created_at: datetime | None = None


class LogisticsQueryResult(StrictBusinessModel):
    """Safe logistics fields; tracking numbers must already be masked."""

    order_no: OrderNumber
    status: ShortBusinessText
    carrier: ShortBusinessText
    tracking_no_masked: MaskedTrackingNumber
    updated_at: datetime | None = None


class OwnedOrderQueryResult(StrictBusinessModel):
    """Provider result paired with ownership evidence."""

    ownership: OrderOwnership
    result: OrderQueryResult


class OwnedLogisticsQueryResult(StrictBusinessModel):
    """Provider logistics result paired with ownership evidence."""

    ownership: OrderOwnership
    result: LogisticsQueryResult


BusinessQueryOperation = Literal["order_query", "logistics_query"]
BusinessQueryOutcome = Literal["success", "denied", "timeout", "failed"]


class BusinessQueryAuditEvent(StrictBusinessModel):
    """PII-minimized audit event for one business query attempt."""

    tenant_id: UUID
    conversation_id: Identifier
    request_id: Identifier
    actor_id: Identifier
    operation: BusinessQueryOperation
    outcome: BusinessQueryOutcome
    visitor_fingerprint: HexDigest
    parameter_fingerprint: HexDigest
    duration_ms: float = Field(ge=0)


class BusinessQueryContextRequest(BusinessTransportModel):
    """HTTP representation of trusted caller identity."""

    tenant_id: UUID
    visitor_id: Identifier
    external_customer_id: Identifier | None = None
    conversation_id: Identifier
    request_id: Identifier
    actor_id: Identifier

    def to_domain(self) -> BusinessQueryContext:
        return BusinessQueryContext.model_validate(self.model_dump())


class BusinessQueryRequest(BusinessTransportModel):
    """Internal HTTP request for one safe read-only business operation."""

    context: BusinessQueryContextRequest
    operation: BusinessQueryOperation
    order_no: OrderNumber


class BusinessQueryResponse(StrictBusinessModel):
    """Exactly one typed result is returned for the requested operation."""

    operation: BusinessQueryOperation
    order: OrderQueryResult | None = None
    logistics: LogisticsQueryResult | None = None


class BusinessQueryProviderStatus(StrictBusinessModel):
    """Non-secret readiness information for the configured provider."""

    mode: Literal["disabled", "demo", "http"]
    configured: bool
    auth_mode: Literal["none", "bearer", "api_key", "basic"] | None = None
    method: Literal["GET", "POST"] | None = None
    order_path: str | None = None
    logistics_path: str | None = None
    timeout_seconds: float
