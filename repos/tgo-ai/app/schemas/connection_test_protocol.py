"""Only the API owner requests and receives a bounded, identified query test."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Operation = Literal[
    "order_query",
    "logistics_query",
    "customer_profile_query",
    "membership_query",
]
Outcome = Literal["passed", "failed", "demo"]
ErrorCode = Literal[
    "denied",
    "timeout",
    "provider_failed",
    "audit_unavailable",
    "authority_changed",
    "not_found",
    "not_shipped",
    "authentication_failed",
    "business_failed",
]


class ConnectionTestCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    actor_id: UUID
    connection_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    configuration_revision: int = Field(ge=1, strict=True)
    authorization_revision: int = Field(ge=1, strict=True)
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: UUID
    operation: Operation
    order_no: str | None = Field(
        default=None,
        min_length=4,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    customer_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$",
    )

    @model_validator(mode="after")
    def operation_inputs(self) -> Self:
        if (
            self.operation in {"order_query", "logistics_query"}
            and self.order_no is None
        ):
            raise ValueError(
                "order number is required for order and logistics tests"
            )
        if (
            self.operation in {"customer_profile_query", "membership_query"}
            and self.order_no is not None
        ):
            raise ValueError("customer tests cannot include an order number")
        return self


class ConnectionTestSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(min_length=1, max_length=128)
    amount_minor: int | None = Field(default=None, ge=0, strict=True)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    carrier: str | None = Field(default=None, max_length=128)
    tracking_no_masked: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9-]{0,16}\*{2,}[A-Za-z0-9*-]{0,16}$"
    )
    segment: str | None = Field(default=None, max_length=128)
    service_tags: list[str] | None = Field(default=None, max_length=8)
    tier: str | None = Field(default=None, max_length=128)
    points_balance: int | None = Field(default=None, ge=0, strict=True)


class ConnectionTestReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal["business_connection_test"]
    can_enable: Literal[False]
    project_id: UUID
    actor_id: UUID
    connection_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    configuration_revision: int = Field(ge=1, strict=True)
    authorization_revision: int = Field(ge=1, strict=True)
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: UUID
    operation: Operation
    mode: Literal["http", "demo"]
    outcome: Outcome
    summary: ConnectionTestSummary | None
    error_code: ErrorCode | None
    checked_at: datetime

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.checked_at.tzinfo is None:
            raise ValueError("Test receipt time must be timezone-aware")
        if (
            (self.outcome == "failed") != (self.error_code is not None)
            or (self.outcome == "failed") != (self.summary is None)
            or (self.mode == "demo" and self.outcome == "passed")
            or (self.mode == "http" and self.outcome == "demo")
        ):
            raise ValueError("Test result cannot imply unsupported success")
        return self

    def matches(self, command: ConnectionTestCommand) -> bool:
        names = (
            "project_id",
            "actor_id",
            "connection_id",
            "expected_revision",
            "configuration_revision",
            "authorization_revision",
            "resource_revision",
            "request_id",
            "operation",
        )
        return all(
            getattr(self, name) == getattr(command, name) for name in names
        )
