"""Read-only readiness for independently frozen G4 shadow evidence."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


HiddenShadowGateBlocker = Literal[
    "window_not_frozen",
    "empty_sample",
    "manifest_inconsistent",
    "review_incomplete",
    "evidence_changed",
    "evidence_missing",
    "dispute_unresolved",
    "severe_history",
]


class HiddenShadowGateAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_id: UUID
    status: Literal["blocked", "review_complete"]
    blockers: tuple[HiddenShadowGateBlocker, ...]
    can_request_g4_signoff: bool
    requires_live_channel_verification: Literal[True] = True
    integrity_consistent: bool | None
    sample_count: int | None
    completed_cases: int | None
    severe_cases: int | None
    insufficient_cases: int | None
    disputed_cases: int | None
    adjudicated_cases: int | None
    unresolved_disputes: int | None
    review_evidence_sha256: str | None
    assessed_at: datetime
