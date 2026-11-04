"""Conservative, read-only readiness for an official G4 shadow review."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


ShadowGateBlocker = Literal[
    "window_not_frozen",
    "empty_sample",
    "manifest_inconsistent",
    "severe_history",
    "evidence_missing",
    "review_incomplete",
    "official_channel_required",
    "independent_shadow_missing",
]


class ShadowGateAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_id: UUID
    batch_id: UUID | None
    status: Literal["blocked", "review_complete"]
    blockers: tuple[ShadowGateBlocker, ...]
    can_request_g4_signoff: bool
    requires_live_channel_verification: Literal[True] = True
    integrity_consistent: bool | None
    sample_count: int | None
    ready_count: int | None
    historical_severe_count: int | None
    assessed_at: datetime
