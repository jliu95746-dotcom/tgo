"""API contracts for independent review of frozen private AI answers."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.shadow_review import ShadowReason, ShadowVerdict


class HiddenShadowCaseDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_id: UUID
    candidate_id: UUID
    visitor_id: UUID
    selected_position: int
    source_message_id: str
    source_content_sha256: str
    answer: str
    staff_reply: str
    evidence_sha256: str


class HiddenShadowSourceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_id: UUID
    candidate_id: UUID
    source_message_id: str
    content: str
    content_sha256: str
    message_seq: int
    verified_at: datetime


class HiddenShadowReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    verdict: ShadowVerdict
    reason_code: ShadowReason | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_verdict(self) -> "HiddenShadowReviewRequest":
        if (
            self.verdict in ("minor_issue", "severe_error")
            and self.reason_code is None
        ):
            raise ValueError("发现问题时请选择原因")
        if self.reason_code == "other" and not (self.note or "").strip():
            raise ValueError("其他原因需要填写说明")
        if self.note is not None:
            self.note = self.note.strip() or None
        return self


class HiddenShadowReviewReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    protocol_id: UUID
    candidate_id: UUID
    reviewer_id: UUID
    evidence_sha256: str
    source_verified_at: datetime | None
    source_message_seq: int | None
    source_content_sha256: str | None
    verdict: ShadowVerdict
    reason_code: ShadowReason | None
    note: str | None
    created_at: datetime


class HiddenShadowReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_id: UUID
    selected_cases: int
    completed_cases: int
    review_complete: bool
    severe_cases: int | None
    disputed_cases: int | None
    insufficient_cases: int | None
