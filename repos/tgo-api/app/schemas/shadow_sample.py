"""Administrator contracts for frozen G4 shadow review samples."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ShadowSampleStatus = Literal[
    "unreviewed",
    "needs_second",
    "agreed",
    "adjudicated",
    "disputed",
    "insufficient",
    "severe_flagged",
    "missing",
]


class ShadowSampleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    days: int = Field(ge=1, le=90)
    sample_size: int = Field(ge=1, le=500)


class ShadowSampleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int
    draft_id: UUID
    visitor_id: UUID
    reviewer_count: int
    review_status: ShadowSampleStatus
    review_set_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    adjudication_verdict: Literal[
        "acceptable", "minor_issue", "severe_error"
    ] | None = None


class ShadowSampleBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    request_id: UUID
    created_at: datetime
    window_start: datetime
    window_end: datetime
    algorithm: str
    random_seed: str = Field(pattern=r"^[0-9a-f]{32}$")
    population_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    population_count: int
    requested_size: int
    sample_count: int
    reviewed_count: int
    insufficient_count: int
    unreviewed_count: int
    current_severe_count: int
    ever_severe_count: int
    missing_drafts_count: int
    ready_count: int
    resolved_count: int
    needs_second_count: int
    disputed_count: int
    insufficient_case_count: int
    severe_flagged_count: int
    items: tuple[ShadowSampleItem, ...]


class ShadowSampleBatchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    created_at: datetime
    window_start: datetime
    window_end: datetime
    population_count: int
    requested_size: int
    sample_count: int


class ShadowSampleBatchList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[ShadowSampleBatchSummary, ...]
    has_more: bool


class ShadowProtocolCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    window_start: datetime
    window_end: datetime
    sample_size: int = Field(ge=1, le=500)
    # None is retained for replaying historical all-channel protocol requests.
    target_channel: Literal["wecom_kf"] | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "ShadowProtocolCreateRequest":
        if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
            raise ValueError("正式影子验收时间窗必须包含时区")
        duration = self.window_end - self.window_start
        if not timedelta(0) < duration <= timedelta(days=90):
            raise ValueError("正式影子验收时间窗必须大于零且不超过 90 天")
        return self


ShadowProtocolStatus = Literal["scheduled", "frozen", "empty"]


class ShadowProtocolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    request_id: UUID
    created_at: datetime
    window_start: datetime
    window_end: datetime
    sample_size: int
    algorithm: str
    target_channel: Literal["wecom_kf", "all"]
    seed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ShadowProtocolStatus
    finalized_at: datetime | None
    batch_id: UUID | None
    population_count: int | None
    population_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    manifest_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class ShadowProtocolList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[ShadowProtocolResponse, ...]
    has_more: bool


class ShadowProtocolCandidateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: UUID
    visitor_id: UUID
    selected_position: int | None


class ShadowProtocolCandidatePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_id: UUID
    total_candidates: int
    population_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: tuple[ShadowProtocolCandidateItem, ...]
    next_cursor: UUID | None


class ShadowProtocolIntegrityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_id: UUID
    consistent: bool
    population_valid: bool
    manifest_valid: bool
    sample_valid: bool
    metadata_valid: bool
    population_count: int
    recorded_population_count: int | None
    population_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_population_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_manifest_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
