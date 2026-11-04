"""Business data and verified source metadata; never raw HTTP headers."""

from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class WebhookDeliveryEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, JsonValue]


class VerifiedWebhookDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    hook_id: UUID
    credential_revision: int = Field(ge=1, strict=True)
    event_id: str
    key_id: str
    data: dict[str, JsonValue]


class WebhookPayloadScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    delivery_id: UUID
    hook_id: UUID
    project_id: UUID
    workflow_id: str = Field(min_length=1, max_length=128)
    actor_staff_id: UUID
    binding_revision: int = Field(ge=1, strict=True)
    credential_revision: int = Field(ge=1, strict=True)
    event_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_id: str = Field(min_length=1, max_length=128)
    release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


DeliveryStatus = Literal[
    "pending",
    "queued",
    "completed",
    "failed",
    "cancelled",
    "expired",
    "blocked",
]


class WebhookAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delivery_id: UUID
    status: DeliveryStatus
    execution_id: UUID | None = None
    deduplicated: bool
