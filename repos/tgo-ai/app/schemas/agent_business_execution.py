"Formal customer queries are separate from configuration and employee \
tests."

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    TypeAdapter,
    model_validator,
)

from app.schemas.agent_execution import AgentExecutionReference
from app.schemas.business_execution_context import BusinessContextCheck
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
json_object = TypeAdapter(dict[str, JsonValue])


class BusinessExecutionIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal[
        "employee_business_execution"
    ] = "employee_business_execution"
    can_enable: Literal[False] = False
    query_id: UUID
    reference: AgentExecutionReference
    connection_id: UUID
    binding_id: UUID
    binding_revision: int = Field(ge=1, strict=True)
    operation: Operation
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    def matches(self, command: "BusinessExecutionCommand") -> bool:
        return (
            self.model_dump(
                mode="json",
                include=set(BusinessExecutionIdentity.model_fields),
            )
            == command.identity()
        )


class BusinessExecutionCommand(BusinessExecutionIdentity):
    context: BusinessContextCheck
    order_no: SecretStr | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def same_request_scope(self) -> "BusinessExecutionCommand":
        if (
            self.reference.project_id != self.context.project_id
            or self.reference.channel != self.context.channel
            or not self.reference.business_evidence_revision
        ):
            raise ValueError("原执行业务范围不一致")
        if self.operation in {"order_query", "logistics_query"}:
            if (
                self.order_no is None
                or not self.order_no.get_secret_value().strip()
            ):
                raise ValueError("订单和物流查询必须提供订单号")
        elif self.order_no is not None:
            raise ValueError("会员和客户资料查询不能包含订单号")
        return self

    def identity(self) -> dict[str, JsonValue]:
        return json_object.validate_python(
            self.model_dump(
                mode="json",
                include=set(BusinessExecutionIdentity.model_fields),
            )
        )

    def private_payload(self) -> dict[str, JsonValue]:
        value = {
            **self.identity(),
            "context": {
                **self.context.model_dump(mode="json"),
                "context_token": self.context.context_token.get_secret_value(),
            },
        }
        if self.order_no is not None:
            value["order_no"] = self.order_no.get_secret_value()
        return value


class BusinessExecutionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    command: BusinessExecutionCommand
    phase: Literal["before_query", "after_query"]

    def private_payload(self) -> dict[str, JsonValue]:
        return {"command": self.command.private_payload(), "phase": self.phase}


class BusinessExecutionAuthority(BusinessExecutionIdentity):
    can_query: Literal[True] = True
    actor_id: UUID
    phase: Literal["before_query", "after_query"]
    connection_revision: int = Field(ge=1, strict=True)
    configuration_revision: int = Field(ge=1, strict=True)
    authorization_revision: int = Field(ge=1, strict=True)
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_id: UUID
    review_id: UUID
    configuration_test_id: UUID
    checked_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def current_authority(self) -> "BusinessExecutionAuthority":
        if (
            self.checked_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.checked_at
        ):
            raise ValueError("正式查询授权已失效")
        return self


class BusinessExecutionReceipt(BusinessExecutionIdentity):
    authority: BusinessExecutionAuthority
    outcome: Literal["passed", "failed"]
    summary: ConnectionTestSummary | None
    error_code: ErrorCode | None
    checked_at: datetime

    @model_validator(mode="after")
    def exact_current_receipt(self) -> "BusinessExecutionReceipt":
        if (
            self.model_dump(
                mode="json",
                include=set(BusinessExecutionIdentity.model_fields),
            )
            != self.authority.model_dump(
                mode="json",
                include=set(BusinessExecutionIdentity.model_fields),
            )
            or self.authority.phase != "after_query"
            or self.checked_at.tzinfo is None
            or self.authority.expires_at <= self.checked_at
            or (
                self.outcome == "passed"
                and (self.summary is None or self.error_code is not None)
            )
            or (
                self.outcome == "failed"
                and (self.summary is not None or self.error_code is None)
            )
        ):
            raise ValueError("正式查询结果范围或状态不完整")
        if (
            self.outcome == "passed"
            and self.operation == "membership_query"
            and (
                self.summary is None
                or not self.summary.tier
                or self.summary.points_balance is None
            )
        ):
            raise ValueError("会员查询结果缺少等级或积分")
        if (
            self.outcome == "passed"
            and self.operation == "customer_profile_query"
            and (self.summary is None or self.summary.service_tags is None)
        ):
            raise ValueError("客户资料结果缺少服务标签")
        return self


class BusinessRuntimeCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "business_execution_runtime_capabilities"
    ] = "business_execution_runtime_capabilities"
    can_enable: Literal[False] = False
    protocol_version: Literal[2] = 2
    operations: list[Operation] = Field(min_length=4, max_length=4)
    checked_at: datetime

    @model_validator(mode="after")
    def complete_capabilities(self) -> "BusinessRuntimeCapabilities":
        if self.checked_at.tzinfo is None or set(self.operations) != {
            "order_query",
            "logistics_query",
            "customer_profile_query",
            "membership_query",
        }:
            raise ValueError("业务运行协议不完整")
        return self


class BusinessRuntimeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    requested_by: UUID


class BusinessRuntimeReadiness(BusinessRuntimeQuery):
    scope: Literal["employee_business_runtime"] = "employee_business_runtime"
    can_enable: Literal[False] = False
    available: bool = Field(strict=True)
    protocol_version: Literal[2] = 2
    operations: list[Operation] = Field(max_length=4)
    checked_at: datetime

    @model_validator(mode="after")
    def coherent_runtime(self) -> "BusinessRuntimeReadiness":
        if self.checked_at.tzinfo is None or len(set(self.operations)) != len(
            self.operations
        ):
            raise ValueError("业务运行能力返回不完整")
        if self.available and set(self.operations) != {
            "order_query",
            "logistics_query",
            "customer_profile_query",
            "membership_query",
        }:
            raise ValueError("业务运行操作尚未全部可用")
        return self
