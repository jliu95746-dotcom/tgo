"""Exact, bounded fields for an internal aftersales preparation receipt."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)


ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
ReasonText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)
]
PolicyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class AfterSalesIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    order_reference: ShortText
    sku: ShortText
    request_reason: ReasonText
    policy_reference: PolicyText
    evidence_file_ids: list[UUID] = Field(max_length=5)

    @field_validator("evidence_file_ids")
    @classmethod
    def unique_files(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("售后登记附件不可重复")
        return values


class AfterSalesIntakeSummary(BaseModel):
    review_id: str
    order_reference: str
    sku: str
    request_reason: str
    policy_reference: str
    evidence_file_ids: list[str]
    prepared_by: str
    prepared_at: datetime
    status: Literal["prepared_internal"] = "prepared_internal"
    external_submission_status: Literal["not_submitted"] = "not_submitted"
