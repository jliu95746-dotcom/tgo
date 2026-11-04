"""Private, request-scoped read queries from workflow to AI."""

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, field_validator


BusinessOperation = Literal["order_query", "logistics_query"]
BusinessChannel = Literal["wecom_kf", "web", "app", "phone", "internal"]
BusinessErrorCode = Literal[
    "denied", "timeout", "provider_failed", "audit_unavailable",
    "authority_changed", "not_found", "not_shipped", "authentication_failed",
    "business_failed",
]
ORDER_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,63}$")


class BusinessExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    request_id: UUID
    visitor_session_id: UUID
    visitor_id: UUID
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel: BusinessChannel
    context_token: SecretStr = Field(min_length=1, max_length=16384,
                                     exclude=True, repr=False)


class BusinessQuerySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=128)
    amount_minor: int | None = Field(default=None, ge=0, strict=True)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    carrier: str | None = Field(default=None, max_length=128)
    tracking_no_masked: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9-]{0,16}\*{2,}[A-Za-z0-9*-]{0,16}$",
    )


class BusinessQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["workflow_business_query"] = "workflow_business_query"
    can_enable: Literal[False] = False
    project_id: UUID
    workflow_execution_id: UUID
    query_id: UUID
    connection_id: UUID
    operation: BusinessOperation
    outcome: Literal["passed", "failed"]
    summary: BusinessQuerySummary | None
    error_code: BusinessErrorCode | None


class BusinessQueryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    agent_id: UUID
    workflow_execution_id: UUID
    connection_id: UUID
    operation: BusinessOperation
    visitor_session_id: UUID
    order_no: SecretStr = Field(min_length=4, max_length=64, exclude=True,
                                repr=False)
    context: BusinessExecutionContext

    @field_validator("order_no")
    @classmethod
    def valid_order_no(cls, value: SecretStr) -> SecretStr:
        if not ORDER_NUMBER.fullmatch(value.get_secret_value()):
            raise ValueError("订单号格式无效")
        return value

    def private_payload(self) -> dict[str, JsonValue]:
        return {
            "project_id": str(self.project_id),
            "agent_id": str(self.agent_id),
            "workflow_execution_id": str(self.workflow_execution_id),
            "connection_id": str(self.connection_id),
            "operation": self.operation,
            "visitor_session_id": str(self.visitor_session_id),
            "order_no": self.order_no.get_secret_value(),
            "context": {
                "project_id": str(self.context.project_id),
                "visitor_id": str(self.context.visitor_id),
                "channel": self.context.channel,
                "request_id": str(self.context.request_id),
                "request_fingerprint": self.context.request_fingerprint,
                "context_token": self.context.context_token.get_secret_value(),
            },
        }
