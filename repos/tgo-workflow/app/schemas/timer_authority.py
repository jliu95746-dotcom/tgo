"""Echo-bound current permission check for one timer source fact."""

from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class TimerAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    actor_staff_id: UUID
    workflow_id: str = Field(min_length=1, max_length=128)
    schedule_id: UUID
    revision: int = Field(ge=1, strict=True)
    occurrence_id: UUID
    scheduled_at: AwareDatetime
    release_id: str = Field(min_length=1, max_length=128)
    release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
