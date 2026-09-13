"""Read-only device monitoring contract, excluding sensitive tool contents."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class DeviceSessionSummary(BaseModel):
    id: UUID
    device_id: UUID
    device_name: str
    agent_id: UUID | None
    agent_name: str | None
    status: Literal[
        "running", "completed", "failed", "cancelled", "interrupted"
    ]
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    lease_expires_at: AwareDatetime | None
    actions_count: int = Field(ge=0)
    failed_actions_count: int = Field(ge=0)
    screenshots_count: int = Field(ge=0)


class DeviceSessionStep(BaseModel):
    id: UUID
    tool_name: str
    status: Literal["running", "completed", "failed", "interrupted"]
    started_at: AwareDatetime
    ended_at: AwareDatetime | None


class DeviceSessionDetail(DeviceSessionSummary):
    steps: list[DeviceSessionStep] = Field(max_length=100)
    step_total: int = Field(ge=0)


class DeviceSessionList(BaseModel):
    sessions: list[DeviceSessionSummary] = Field(max_length=100)
    total: int = Field(ge=0)
