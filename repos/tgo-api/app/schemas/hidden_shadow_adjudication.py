"""Blind-review dispute and third-person adjudication contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.hidden_shadow_review import HiddenShadowReviewReceipt
from app.schemas.shadow_review import ShadowReason


AdjudicationVerdict = Literal[
    "acceptable", "minor_issue", "severe_error",
]


class HiddenShadowAdjudicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    expected_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: AdjudicationVerdict
    reason_code: ShadowReason | None = None
    note: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_reason(self) -> "HiddenShadowAdjudicationRequest":
        self.note = self.note.strip()
        if not self.note:
            raise ValueError("裁决说明不能为空")
        if (
            self.verdict in ("minor_issue", "severe_error")
            and self.reason_code is None
        ):
            raise ValueError("发现问题时请选择原因")
        if self.reason_code == "other" and len(self.note) < 5:
            raise ValueError("其他原因需填写具体依据")
        return self


class HiddenShadowAdjudicationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    protocol_id: UUID
    candidate_id: UUID
    adjudicator_id: UUID
    request_id: UUID
    review_set_sha256: str
    evidence_sha256: str
    verdict: AdjudicationVerdict
    reason_code: ShadowReason | None
    note: str
    created_at: datetime


class HiddenShadowReviewPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_id: UUID
    candidate_id: UUID
    visitor_id: UUID
    evidence_sha256: str
    review_set_sha256: str
    reviews: tuple[HiddenShadowReviewReceipt, HiddenShadowReviewReceipt]
    adjudication: HiddenShadowAdjudicationReceipt | None
