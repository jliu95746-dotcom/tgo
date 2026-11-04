"""Private adapter-only credentials and verified customer for one test claim."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, Field, field_serializer

from app.schemas.agent_business_trials import (
    BusinessTrialAuthority,
    BusinessTrialCustomerEvidence,
)
from app.schemas.business_connections import BusinessConnectionPrivateSnapshot


class BusinessTrialResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["employee_business_test_resolution"] = (
        "employee_business_test_resolution"
    )
    can_enable: Literal[False] = False
    authority: BusinessTrialAuthority
    snapshot: BusinessConnectionPrivateSnapshot
    customer_evidence: BusinessTrialCustomerEvidence
    customer_id: SecretStr = Field(repr=False)

    @field_serializer("customer_id")
    def private_customer(self, value: SecretStr) -> str:
        return value.get_secret_value()
