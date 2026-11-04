"""Public test metadata never accepts credentials or caller-declared success."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.connection_test_protocol import (
    ConnectionTestSummary,
    Operation,
    Outcome,
)

TestStatus = Literal["running", "completed", "failed", "cancelled"]
ApplicabilityIssue = Literal[
    "test_not_passed",
    "demo_only",
    "configuration_changed",
    "capability_removed",
    "superseded",
    "expired",
    "reviewer_unavailable",
    "credential_unavailable",
    "evidence_unknown",
]


class BusinessConnectionTestApplicability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["business_connection_configuration"] = (
        "business_connection_configuration"
    )
    status: Literal["current", "stale", "not_passed", "unavailable"]
    checked_at: datetime
    valid_until: datetime | None
    issues: list[ApplicabilityIssue]


class BusinessConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    operation: Operation
    order_no: str | None = Field(
        default=None, min_length=4, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )
    customer_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$"
    )

    @model_validator(mode="after")
    def operation_inputs(self) -> Self:
        if self.operation in {"order_query", "logistics_query"} and self.order_no is None:
            raise ValueError("订单与物流测试必须填写订单号")
        if self.operation in {"customer_profile_query", "membership_query"} and self.order_no is not None:
            raise ValueError("客户资料与会员测试不接受订单号")
        return self


class BusinessConnectionTestReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    request_id: UUID
    connection_id: UUID
    operation: Operation
    mode: Literal["http", "demo"]
    status: TestStatus
    outcome: Outcome | None
    configuration_revision: int
    summary: ConnectionTestSummary | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime
    applicability: BusinessConnectionTestApplicability
    scope: Literal["business_connection_test"] = "business_connection_test"
    can_enable: Literal[False] = False
