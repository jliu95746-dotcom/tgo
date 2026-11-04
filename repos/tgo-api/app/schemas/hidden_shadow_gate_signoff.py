"""Receipts for independent G4 signoff requests and decisions."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HiddenShadowGateSignoffReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    protocol_id: UUID
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
    current_evidence_valid: bool | None = None
    decider_id: UUID | None
    decision_request_id: UUID | None
    decision_note: str | None
    decided_at: datetime | None
