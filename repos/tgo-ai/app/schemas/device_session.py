"""Device-control lifecycle contracts, separate from conversation identity."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


DeviceFinishStatus = Literal["completed", "failed", "cancelled"]


class DeviceExecutionIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: UUID
    device_id: UUID
    session_id: UUID
    agent_id: UUID
    agent_name: str = Field(min_length=1, max_length=255)


class DeviceSessionReceipt(BaseModel):
    id: UUID
    device_id: UUID
    agent_id: UUID | None
    status: Literal[
        "running", "completed", "failed", "cancelled", "interrupted"
    ]
    lease_expires_at: AwareDatetime | None
