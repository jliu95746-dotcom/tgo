"Public approval fields; the private workflow checkpoint never crosses \
the API."

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


class WorkflowApprovalAssigneeCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    assignee_ids: list[UUID] = Field(min_length=1, max_length=100)


class WorkflowApprovalAssigneeReceipt(BaseModel):
    project_id: UUID
    assignee_ids: list[UUID]


class WorkflowApprovalSummary(BaseModel):
    id: str
    execution_id: str
    node_id: str
    summary: str
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "approved"]
    requested_at: datetime
    expires_at: datetime
    customer_session_available: bool = False


class WorkflowApprovalCustomerSession(BaseModel):
    visitor_session_id: UUID
    source_message_id: str | None = None
    source_channel: str | None = None


class WorkflowApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkflowApprovalDecisionResult(BaseModel):
    id: str
    execution_id: str
    status: Literal["approved", "rejected"]
    dispatch_status: Literal["queued", "unknown", "not_applicable"]


class WorkflowAfterSalesReviewSummary(BaseModel):
    id: UUID
    execution_id: UUID
    approval_id: UUID
    visitor_session_id: UUID
    category: Literal["return_request", "exchange_request", "quality_issue"]
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending_staff_review", "reviewed"]
    created_at: datetime
    review_result: Literal[
        "needs_manual_service",
        "needs_customer_information",
        "not_aftersales",
    ] | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None


class WorkflowAfterSalesReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: Literal[
        "needs_manual_service",
        "needs_customer_information",
        "not_aftersales",
    ]
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkflowAfterSalesEvidenceFile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    file_type: str
    created_at: datetime


class WorkflowAfterSalesChatTarget(BaseModel):
    channel_type: Literal[251]
    channel_id: str = Field(min_length=40, max_length=40)
    message_seq: int | None = Field(default=None, gt=0)


IntakeShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
IntakeReasonText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=5, max_length=500),
]
IntakePolicyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class WorkflowAfterSalesIntakeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    order_reference: IntakeShortText
    sku: IntakeShortText
    request_reason: IntakeReasonText
    policy_reference: IntakePolicyText
    evidence_file_ids: list[UUID] = Field(max_length=5)

    @field_validator("evidence_file_ids")
    @classmethod
    def unique_files(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("售后登记附件不可重复")
        return values


class WorkflowAfterSalesIntakeSummary(BaseModel):
    review_id: UUID
    order_reference: str
    sku: str
    request_reason: str
    policy_reference: str
    evidence_file_ids: list[UUID]
    prepared_by: UUID
    prepared_at: datetime
    status: Literal["prepared_internal"]
    external_submission_status: Literal["not_submitted"]


class WorkflowAfterSalesHandoffSummary(BaseModel):
    request_id: UUID
    status: str = Field(min_length=1, max_length=32)
    notification_status: Literal["submitted", "unknown"]
