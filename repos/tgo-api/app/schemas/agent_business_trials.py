"""Purpose-limited business tests; private inputs are never report fields."""

import re
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    TypeAdapter,
    field_validator,
    model_validator,
)
from app.schemas.connection_test_protocol import (
    ConnectionTestSummary,
    ErrorCode,
)

Operation = Literal[
    "order_query",
    "logistics_query",
    "customer_profile_query",
    "membership_query",
]
TrialError = Literal[
    "denied",
    "timeout",
    "provider_failed",
    "audit_unavailable",
    "authority_changed",
    "not_found",
    "not_shipped",
    "authentication_failed",
    "business_failed",
    "interrupted",
    "cancelled",
    "result_unknown",
]
json_object = TypeAdapter(dict[str, JsonValue])


class BusinessTrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    connection_id: UUID
    operation: Operation
    visitor_id: UUID
    order_no: SecretStr | None = None
    customer_session: SecretStr = Field(
        min_length=8, max_length=8192, repr=False
    )

    @field_validator("order_no")
    @classmethod
    def safe_order(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{3,63}", value.get_secret_value()
        ):
            raise ValueError("请填写有效的测试订单号")
        return value

    @model_validator(mode="after")
    def operation_inputs(self) -> Self:
        if (
            self.operation in {"order_query", "logistics_query"}
            and self.order_no is None
        ):
            raise ValueError("订单和物流业务测试必须提供订单号")
        if (
            self.operation in {"customer_profile_query", "membership_query"}
            and self.order_no is not None
        ):
            raise ValueError("客户资料和会员业务测试不能包含订单号")
        return self

    def private_payload(self) -> dict[str, JsonValue]:
        value = json_object.validate_python(
            self.model_dump(
                mode="json", exclude={"order_no", "customer_session"}
            )
        )
        value["customer_session"] = self.customer_session.get_secret_value()
        if self.order_no is not None:
            value["order_no"] = self.order_no.get_secret_value()
        return value


class BusinessTrialIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    connection_id: UUID
    report_id: UUID
    request_id: UUID
    attempt_id: UUID
    operation: Operation
    binding_id: UUID
    binding_revision: int = Field(ge=1, strict=True)
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=1, strict=True)
    configuration_revision: int = Field(ge=1, strict=True)
    authorization_revision: int = Field(ge=1, strict=True)
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_test_id: UUID
    configuration_test_valid_until: datetime

    @field_validator("configuration_test_valid_until")
    @classmethod
    def aware_proof_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("配置测试时间缺少时区")
        return value

    def matches(self, command: "BusinessTrialCommand") -> bool:
        return all(
            getattr(self, key) == getattr(command, key)
            for key in BusinessTrialIdentity.model_fields
        )


class BusinessTrialCommand(BusinessTrialIdentity):
    purpose: Literal["employee_business_test"] = "employee_business_test"
    visitor_id: UUID
    order_no: SecretStr | None = None
    customer_session: SecretStr = Field(
        min_length=8, max_length=8192, repr=False
    )

    @field_validator("order_no")
    @classmethod
    def safe_order(cls, value: SecretStr | None) -> SecretStr | None:
        return BusinessTrialRequest.safe_order(value)

    @model_validator(mode="after")
    def operation_inputs(self) -> Self:
        if (
            self.operation in {"order_query", "logistics_query"}
            and self.order_no is None
        ):
            raise ValueError("订单和物流业务测试必须提供订单号")
        if (
            self.operation in {"customer_profile_query", "membership_query"}
            and self.order_no is not None
        ):
            raise ValueError("客户资料和会员业务测试不能包含订单号")
        return self

    def private_payload(self) -> dict[str, JsonValue]:
        value = json_object.validate_python(
            self.model_dump(
                mode="json", exclude={"order_no", "customer_session"}
            )
        )
        value["customer_session"] = self.customer_session.get_secret_value()
        if self.order_no is not None:
            value["order_no"] = self.order_no.get_secret_value()
        return value

    def request(self) -> BusinessTrialRequest:
        return BusinessTrialRequest.model_validate(
            {
                name: getattr(self, name)
                for name in BusinessTrialRequest.model_fields
            }
        )

    def identity(self) -> dict[str, JsonValue]:
        return json_object.validate_python(
            self.model_dump(
                mode="json", include=set(BusinessTrialIdentity.model_fields)
            )
        )


class BusinessTrialCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: BusinessTrialCommand
    phase: Literal["before_query", "after_query"]

    def private_payload(self) -> dict[str, JsonValue]:
        return {"command": self.command.private_payload(), "phase": self.phase}


class BusinessTrialAuthority(BusinessTrialIdentity):
    scope: Literal[
        "employee_business_test_authorization"
    ] = "employee_business_test_authorization"
    can_enable: Literal[False] = False
    can_query_test: Literal[True] = True
    phase: Literal["before_query", "after_query"]
    checked_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def current_window(self) -> Self:
        if (
            self.checked_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.checked_at
            or self.configuration_test_valid_until <= self.checked_at
        ):
            raise ValueError("业务测试授权已失效")
        return self


class BusinessTrialCustomerEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    binding_id: UUID
    binding_revision: int = Field(ge=1, strict=True)
    policy_revision: int = Field(ge=1, strict=True)
    expires_at: datetime


class BusinessTrialReceipt(BusinessTrialIdentity):
    scope: Literal["employee_business_trial"] = "employee_business_trial"
    can_enable: Literal[False] = False
    mode: Literal["http"] = "http"
    outcome: Literal["passed", "failed"]
    summary: ConnectionTestSummary | None
    error_code: ErrorCode | None
    customer_evidence: BusinessTrialCustomerEvidence
    checked_at: datetime

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (
            self.checked_at.tzinfo is None
            or self.customer_evidence.expires_at.tzinfo is None
            or self.customer_evidence.expires_at <= self.checked_at
            or self.configuration_test_valid_until <= self.checked_at
        ):
            raise ValueError("业务测试时间依据无法确认")
        if (self.outcome == "failed") != (self.error_code is not None) or (
            self.outcome == "failed"
        ) != (self.summary is None):
            raise ValueError("业务测试结果不能表示未经验证的成功")
        if (
            self.outcome == "passed"
            and self.operation == "membership_query"
            and (
                self.summary is None
                or not self.summary.tier
                or self.summary.points_balance is None
            )
        ):
            raise ValueError("会员业务测试缺少等级或积分结果")
        if (
            self.outcome == "passed"
            and self.operation == "customer_profile_query"
            and (self.summary is None or self.summary.service_tags is None)
        ):
            raise ValueError("客户资料业务测试缺少服务标签结果")
        return self


class BusinessTrialReport(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    scope: Literal["employee_business_trial"] = "employee_business_trial"
    can_enable: Literal[False] = False
    assessment: Literal["not_reviewed"] = "not_reviewed"
    id: UUID
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    connection_id: UUID
    request_id: UUID
    operation: Operation
    status: Literal["running", "completed", "failed", "cancelled"]
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    expires_at: datetime
    completed_at: datetime | None
    error_code: TrialError | None
    receipt: BusinessTrialReceipt | None = None

    @field_validator("created_at", "expires_at", "completed_at")
    @classmethod
    def normalized_time(cls, value: datetime | None) -> datetime | None:
        return (
            value.replace(tzinfo=UTC)
            if value is not None and value.tzinfo is None
            else value
        )

    @model_validator(mode="after")
    def consistent_report(self) -> Self:
        if self.status == "running":
            if (
                self.receipt is not None
                or self.error_code is not None
                or self.completed_at is not None
            ):
                raise ValueError("业务测试运行状态与结果不一致")
        elif self.status == "completed":
            if (
                self.receipt is None
                or self.completed_at is None
                or self.error_code != self.receipt.error_code
            ):
                raise ValueError("业务测试缺少完整结果")
            expected = {
                "report_id": self.id,
                "project_id": self.project_id,
                "actor_id": self.actor_id,
                "agent_id": self.agent_id,
                "connection_id": self.connection_id,
                "request_id": self.request_id,
                "operation": self.operation,
                "configuration_fingerprint": self.configuration_fingerprint,
            }
            if any(
                getattr(self.receipt, key) != value
                for key, value in expected.items()
            ):
                raise ValueError("业务测试结果不属于原报告")
        elif (
            self.receipt is not None
            or self.error_code is None
            or self.completed_at is None
        ):
            raise ValueError("业务测试终态缺少错误分类")
        return self
