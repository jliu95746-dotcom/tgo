"""Employee-visible shadow classification and confirmed human replies."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.assist_trace import AssistRunTrace

SourceProofStatus = Literal[
    "verified",
    "unverified",
    "unsupported",
    "unavailable",
    "missing",
    "mismatch",
]


class ShadowSourceProof(BaseModel):
    """Server IM evidence; never includes the customer's message body."""

    model_config = ConfigDict(extra="forbid")

    status: SourceProofStatus
    checked_at: datetime
    channel_id: str | None = None
    channel_type: int | None = None
    client_msg_no: str | None = None
    message_id: str | None = None
    message_seq: int | None = None
    message_timestamp: int | None = None
    content_sha256: str | None = None


ShadowVerdict = Literal[
    "acceptable", "minor_issue", "severe_error", "insufficient_evidence"
]
ShadowReason = Literal[
    "wrong_fact", "privacy", "unsafe_action", "missing_handoff", "other"
]


class ShadowReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: UUID
    visitor_id: UUID
    request_id: UUID
    verdict: ShadowVerdict
    reason_code: ShadowReason | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_reason_for_errors(self) -> "ShadowReviewRequest":
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


class ShadowReviewReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    draft_id: UUID
    reviewer_id: UUID | None
    verdict: ShadowVerdict
    reason_code: ShadowReason | None
    note: str | None
    created_at: datetime


class ShadowReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_start: datetime
    window_end: datetime
    eligible_drafts: int
    evaluated_drafts: int
    insufficient_drafts: int
    unreviewed_drafts: int
    ever_severe_drafts: int
    current_severe_drafts: int
    verified_eligible_drafts: int = 0
    verified_evaluated_drafts: int = 0
    verified_ever_severe_drafts: int = 0


class ShadowPendingReviewItem(BaseModel):
    """Location of a verified case; reply and customer text stay in chat."""

    model_config = ConfigDict(extra="forbid")

    draft_id: UUID
    visitor_id: UUID
    created_at: datetime


class ShadowPendingReviewQueue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[ShadowPendingReviewItem, ...]
    total_pending: int
    next_cursor: UUID | None = None


class ShadowReviewState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: ShadowVerdict
    reason_code: ShadowReason | None
    reviewed_at: datetime


class ShadowStaffReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_msg_no: str
    content: str
    ai_draft: str | None = None
    ai_draft_id: UUID | None = None
    source_verified: bool = False
    generation_trace: tuple[AssistRunTrace, ...] = ()
    review: ShadowReviewState | None = None
    created_at: datetime


class ShadowComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_message_id: str
    intent: str
    confidence: float
    risk_level: str
    recommended_route: str
    need_human: bool
    created_at: datetime
    replies: tuple[ShadowStaffReply, ...]


class ShadowComparisonsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[ShadowComparison, ...]
    has_more: bool
    reply_limit_reached: bool
