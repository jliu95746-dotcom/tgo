"""Administrator contracts for independent G4 sample registration."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HiddenShadowProtocolCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    window_start: datetime
    window_end: datetime
    sample_size: int = Field(ge=1, le=500)

    @model_validator(mode="after")
    def validate_window(self) -> "HiddenShadowProtocolCreateRequest":
        if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
            raise ValueError("影子验收时间必须包含时区")
        duration = self.window_end - self.window_start
        if not timedelta(0) < duration <= timedelta(days=90):
            raise ValueError("影子验收时间窗必须大于零且不超过 90 天")
        return self


class HiddenShadowProtocolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    request_id: UUID
    created_at: datetime
    window_start: datetime
    window_end: datetime
    sample_size: int
    algorithm: str
    seed_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
    )
    random_seed: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    status: Literal["scheduled", "frozen", "empty", "cancelled"]
    finalized_at: datetime | None
    cancelled_at: datetime | None
    cancelled_by: UUID | None
    population_count: int | None
    sample_count: int | None
    population_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
    )
    manifest_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
    )


class HiddenShadowProtocolList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[HiddenShadowProtocolResponse, ...]
    has_more: bool


class HiddenShadowProtocolCaseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID
    visitor_id: UUID
    selected_position: int | None


class HiddenShadowProtocolCasePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_id: UUID
    total_cases: int
    items: tuple[HiddenShadowProtocolCaseItem, ...]
    next_cursor: UUID | None


class HiddenShadowProtocolIntegrityResponse(BaseModel):
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
    recorded_population_sha256: str | None = None
    manifest_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
    )
    recorded_manifest_sha256: str | None = None
