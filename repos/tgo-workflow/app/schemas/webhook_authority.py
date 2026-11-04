"""Fresh API-owned administrator checks for a fixed hook binding."""

from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field


class WebhookAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    check_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    actor_staff_id: UUID
    workflow_id: str = Field(min_length=1, max_length=128)
    hook_id: UUID
    binding_revision: int = Field(ge=1, strict=True)
    release_id: str = Field(min_length=1, max_length=128)
    release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
