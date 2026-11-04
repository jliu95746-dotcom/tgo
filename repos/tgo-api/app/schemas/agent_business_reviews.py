"""Human review is evidence for one operation; it never grants execution."""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)
from app.schemas.agent_business_trials import BusinessTrialCustomerEvidence, Operation

ReviewReason = Literal["wrong_result", "needs_mapping", "scope_mismatch", "other"]
ReviewIssue = Literal[
    "test_not_completed",
    "test_not_passed",
    "report_expired",
    "configuration_changed",
    "identity_changed",
    "identity_unavailable",
    "result_mapping_unverified",
    "report_changed",
    "evidence_unavailable",
    "superseded",
]


class BusinessReviewChecks(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ownership_checked: StrictBool = False
    result_checked: StrictBool = False
    readonly_scope_checked: StrictBool = False

    def confirmed(self) -> bool:
        return (
            self.ownership_checked
            and self.result_checked
            and self.readonly_scope_checked
        )


class BusinessReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    expected_latest_review_id: UUID | None
    decision: Literal["passed", "rejected"]
    checks: BusinessReviewChecks
    reason: ReviewReason | None

    @model_validator(mode="after")
    def correct_reason(self) -> Self:
        if (self.decision == "rejected") != (self.reason is not None):
            raise ValueError("评审结论与原因不一致")
        return self


class BusinessReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal["employee_business_operation_review"] = (
        "employee_business_operation_review"
    )
    can_enable: Literal[False] = False
    id: UUID
    project_id: UUID
    agent_id: UUID
    report_id: UUID
    connection_id: UUID
    operation: Operation
    reviewed_by: UUID
    request_id: UUID
    revision: int = Field(ge=1, strict=True)
    criteria_version: Literal[1] = 1
    report_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["passed", "rejected"]
    checks: BusinessReviewChecks
    reason: ReviewReason | None
    created_at: datetime
    valid_until: datetime

    @field_validator("created_at", "valid_until")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (
            (self.decision == "rejected") != (self.reason is not None)
            or self.decision == "passed"
            and (not self.checks.confirmed() or self.valid_until <= self.created_at)
        ):
            raise ValueError("评审记录不能表示未经确认的通过")
        return self


class BusinessReviewState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["employee_business_operation_review"] = (
        "employee_business_operation_review"
    )
    can_enable: Literal[False] = False
    project_id: UUID
    agent_id: UUID
    report_id: UUID
    checked_by: UUID
    checked_at: datetime
    status: Literal["current", "changed", "not_eligible", "unavailable"]
    review_status: Literal[
        "not_reviewed", "passed", "rejected", "needs_retest", "unavailable"
    ]
    issues: list[ReviewIssue]
    can_review: bool = Field(strict=True)
    valid_until: datetime | None
    latest_review: BusinessReviewRecord | None
    reviews: list[BusinessReviewRecord] = Field(max_length=50)

    @model_validator(mode="after")
    def scoped_state(self) -> Self:
        if self.checked_at.tzinfo is None or self.can_review != (
            self.status == "current"
        ):
            raise ValueError("评审状态缺少当前核验")
        if self.status == "current" and (
            self.issues
            or self.valid_until is None
            or self.valid_until.tzinfo is None
            or self.valid_until <= self.checked_at
        ):
            raise ValueError("当前评审依据已失效")
        for row in self.reviews:
            if (
                row.project_id != self.project_id
                or row.agent_id != self.agent_id
                or row.report_id != self.report_id
                or row.reviewed_by != self.checked_by
            ):
                raise ValueError("评审历史范围不一致")
        if self.latest_review is not None and (
            not self.reviews or self.latest_review != self.reviews[0]
        ):
            raise ValueError("最新评审与历史记录不一致")
        if self.review_status == "passed" and (
            self.status != "current"
            or self.latest_review is None
            or self.latest_review.decision != "passed"
            or self.latest_review.valid_until <= self.checked_at
        ):
            raise ValueError("不能由旧评审表示当前通过")
        return self


class BusinessTestEvidenceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    requested_by: UUID
    connection_id: UUID
    configuration_revision: int = Field(ge=1, strict=True)
    customer_evidence: BusinessTrialCustomerEvidence
    observed_at: datetime

    @model_validator(mode="after")
    def historic_window(self) -> Self:
        if (
            self.observed_at.tzinfo is None
            or self.customer_evidence.expires_at.tzinfo is None
            or self.observed_at >= self.customer_evidence.expires_at
        ):
            raise ValueError("原测试客户身份时间无法核验")
        return self


class BusinessTestEvidence(BusinessTestEvidenceQuery):
    scope: Literal["business_trial_customer_evidence_check"] = (
        "business_trial_customer_evidence_check"
    )
    can_enable: Literal[False] = False
    status: Literal["current", "changed"]
    issues: list[Literal["identity_changed", "identity_unavailable"]]
    checked_at: datetime

    @model_validator(mode="after")
    def coherent_evidence(self) -> Self:
        if self.checked_at.tzinfo is None or (self.status == "current") != (
            not self.issues
        ):
            raise ValueError("客户依据核验状态不一致")
        return self


class BusinessOperationCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding_id: UUID
    binding_revision: int = Field(ge=1, strict=True)
    connection_id: UUID
    operation: Operation
    status: Literal[
        "not_tested",
        "testing",
        "test_failed",
        "review_required",
        "rejected",
        "passed",
        "needs_retest",
        "unavailable",
    ]
    report_id: UUID | None
    review_id: UUID | None
    valid_until: datetime | None
    issues: list[ReviewIssue]


class AgentBusinessCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["employee_business_capabilities"] = "employee_business_capabilities"
    can_enable: Literal[False] = False
    can_execute: Literal[False] = False
    project_id: UUID
    agent_id: UUID
    checked_by: UUID
    checked_at: datetime
    capabilities: list[BusinessOperationCapability] = Field(max_length=200)

    @model_validator(mode="after")
    def safe_capabilities(self) -> Self:
        keys = {(row.binding_id, row.operation) for row in self.capabilities}
        if self.checked_at.tzinfo is None or len(keys) != len(self.capabilities):
            raise ValueError("业务能力核验重复或缺少时间")
        if any(
            row.status == "passed"
            and (
                row.report_id is None
                or row.review_id is None
                or row.issues
                or row.valid_until is None
                or row.valid_until.tzinfo is None
                or row.valid_until <= self.checked_at
            )
            for row in self.capabilities
        ):
            raise ValueError("不能由失效或不完整依据表示能力通过")
        return self
