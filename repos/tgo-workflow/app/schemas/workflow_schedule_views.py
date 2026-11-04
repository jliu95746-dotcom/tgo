"""Read-only timer previews and source history; no customer payloads."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class SchedulePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cron_expression: str = Field(min_length=1, max_length=512, strict=True)
    timezone: str = Field(
        default="UTC", min_length=1, max_length=100, strict=True
    )


class SchedulePreview(BaseModel):
    cron_expression: str
    timezone: str
    computed_at: AwareDatetime
    preview_at: list[AwareDatetime]


class ScheduleReleaseContext(BaseModel):
    release_id: str
    definition_digest: str
    version: int
    cron_expression: str
    timezone: str


class ScheduleOccurrence(BaseModel):
    id: UUID
    revision: int
    scheduled_at: datetime
    status: Literal["pending", "queued", "skipped", "cancelled", "blocked"]
    attempts: int
    error_code: str | None
    last_failure_code: str | None
    execution_id: str | None
    execution_status: str | None
    created_at: datetime


class ScheduleOccurrenceList(BaseModel):
    items: list[ScheduleOccurrence]
    total: int
    limit: int
    offset: int
    has_more: bool
