"""Management contract with gateway-injected actor and project identity."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from app.schemas.workflow_schedule_views import ScheduleReleaseContext
from app.schemas.timer_runtime_health import TimerRuntimeState


class ScheduleSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    expected_release_id: str = Field(min_length=1, max_length=128)
    expected_release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: StrictBool = False


class ScheduleDisable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)


class ScheduleSaveInternal(ScheduleSave):
    project_id: UUID
    actor_staff_id: UUID


class ScheduleDisableInternal(ScheduleDisable):
    project_id: UUID
    actor_staff_id: UUID


class ScheduleSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: UUID
    project_id: UUID
    workflow_id: str
    revision: int = Field(ge=1)
    enabled: bool
    release_id: str
    release_digest: str
    cron_expression: str
    timezone: str
    next_run_at: datetime | None
    created_by: UUID
    updated_by: UUID
    created_at: datetime
    updated_at: datetime


class ScheduleState(BaseModel):
    runtime: TimerRuntimeState | None = None
    current_release: ScheduleReleaseContext | None = None
    schedule: ScheduleSummary | None
    binding_current: bool
    execution_available: bool
    preview_at: list[datetime] = Field(default_factory=list)
    suspension_reason: str | None = None
    suspended_at: datetime | None = None


class ScheduleReceipt(BaseModel):
    request_id: UUID
    operation: Literal["save", "disable"]
    schedule: ScheduleSummary
    replayed: bool
