"""Safe monitoring contracts; never contain tool arguments or raw results."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SessionStatus = Literal[
    "running", "completed", "failed", "cancelled", "interrupted"
]
FinishStatus = Literal["completed", "failed", "cancelled"]
StepStatus = Literal["running", "completed", "failed", "interrupted"]


class SessionStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID
    agent_name: str = Field(min_length=1, max_length=255)


class SessionFinish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: FinishStatus


class SessionSummary(BaseModel):
    id: UUID
    device_id: UUID
    device_name: str
    agent_id: UUID | None
    agent_name: str | None
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None
    lease_expires_at: datetime | None
    actions_count: int
    failed_actions_count: int
    screenshots_count: int


class SessionStep(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tool_name: str
    status: StepStatus
    started_at: datetime
    ended_at: datetime | None


class SessionDetail(SessionSummary):
    steps: list[SessionStep]
    step_total: int


class SessionList(BaseModel):
    sessions: list[SessionSummary]
    total: int
