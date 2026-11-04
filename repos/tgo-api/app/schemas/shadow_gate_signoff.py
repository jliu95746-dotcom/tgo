"""Human signoff evidence for an official frozen G4 shadow sample."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ShadowGateSignoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    evidence_reference: str = Field(
        min_length=6, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
    )
    live_case_count: int = Field(ge=1, le=100000)
    note: str = Field(min_length=10, max_length=500)

    @model_validator(mode="after")
    def strip_note(self) -> "ShadowGateSignoffRequest":
        self.note = self.note.strip()
        if len(self.note) < 10:
            raise ValueError("请填写真实渠道核对说明")
        return self


class ShadowGateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    decision: Literal["accepted", "rejected"]
    note: str = Field(min_length=5, max_length=500)
    live_evidence_verified: bool

    @model_validator(mode="after")
    def strip_note(self) -> "ShadowGateDecisionRequest":
        self.note = self.note.strip()
        if len(self.note) < 5:
            raise ValueError("请填写签收依据")
        return self


class ShadowGateSignoffReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    protocol_id: UUID
    batch_id: UUID
    requester_id: UUID
    request_id: UUID
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_count: int
    evidence_reference: str
    live_case_count: int
    note: str
    created_at: datetime
    status: Literal["pending", "accepted", "rejected"]
    decider_id: UUID | None
    decision_request_id: UUID | None
    decision_note: str | None
    decided_at: datetime | None
