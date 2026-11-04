"""Private workflow read query; customer secrets are never response fields."""

from typing import Literal
import re
from uuid import UUID

from pydantic import (
    BaseModel, ConfigDict, Field, JsonValue, SecretStr, TypeAdapter,
    field_validator, model_validator,
)

from app.schemas.agent_business_connections import BusinessOperation
from app.schemas.business_execution_context import BusinessContextCheck
from app.schemas.connection_test_protocol import (
    ConnectionTestSummary, ErrorCode,
)


json_object = TypeAdapter(dict[str, JsonValue])
ORDER_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,63}$")


class WorkflowBusinessQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    agent_id: UUID
    connection_id: UUID
    operation: BusinessOperation
    workflow_execution_id: UUID
    visitor_session_id: UUID
    order_no: SecretStr = Field(
        min_length=4, max_length=64, repr=False, exclude=True,
    )
    context: BusinessContextCheck

    @field_validator("order_no")
    @classmethod
    def valid_order_number(cls, value: SecretStr) -> SecretStr:
        if not ORDER_NUMBER.fullmatch(value.get_secret_value()):
            raise ValueError("订单号格式无效")
        return value

    @model_validator(mode="after")
    def matching_project(self) -> "WorkflowBusinessQueryRequest":
        if self.project_id != self.context.project_id:
            raise ValueError("工作流业务请求企业范围不一致")
        return self

    def private_payload(self) -> dict[str, JsonValue]:
        return json_object.validate_python({
            **self.model_dump(mode="json"),
            "order_no": self.order_no.get_secret_value(),
            "context": {
                **self.context.model_dump(mode="json"),
                "context_token": self.context.context_token.get_secret_value(),
            },
        })


class WorkflowBusinessQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["workflow_business_query"] = "workflow_business_query"
    can_enable: Literal[False] = False
    project_id: UUID
    workflow_execution_id: UUID
    query_id: UUID
    connection_id: UUID
    operation: BusinessOperation
    outcome: Literal["passed", "failed"]
    summary: ConnectionTestSummary | None
    error_code: ErrorCode | None

    @model_validator(mode="after")
    def coherent_result(self) -> "WorkflowBusinessQueryResult":
        if (
            (self.outcome == "passed") != (self.summary is not None)
            or (self.outcome == "failed") != (self.error_code is not None)
        ):
            raise ValueError("工作流业务查询结果不完整")
        return self
