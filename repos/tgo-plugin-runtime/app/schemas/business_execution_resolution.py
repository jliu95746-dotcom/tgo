"""Only the private adapter receives merchant credentials and customer id\
entity."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer

from app.schemas.agent_business_execution import BusinessExecutionAuthority
from app.schemas.business_connections import BusinessConnectionPrivateSnapshot
from app.schemas.business_execution_context import BusinessRequestPrincipal


class BusinessExecutionResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "employee_business_execution_resolution"
    ] = "employee_business_execution_resolution"
    can_enable: Literal[False] = False
    authority: BusinessExecutionAuthority
    snapshot: BusinessConnectionPrivateSnapshot
    principal: BusinessRequestPrincipal
    customer_id: SecretStr = Field(repr=False)

    @field_serializer("customer_id")
    def private_customer(self, value: SecretStr) -> str:
        return value.get_secret_value()
