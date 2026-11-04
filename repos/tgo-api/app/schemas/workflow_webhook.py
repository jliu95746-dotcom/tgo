"""Management contract; identity and credentials are selected by the server."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    field_serializer,
)


class WebhookSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0, le=2147483646, strict=True)
    expected_release_id: str = Field(min_length=1, max_length=128)
    expected_release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: StrictBool = False


class WebhookChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=1, le=2147483646, strict=True)


class WebhookSaveInternal(WebhookSave):
    project_id: UUID
    actor_staff_id: UUID


class WebhookChangeInternal(WebhookChange):
    project_id: UUID
    actor_staff_id: UUID


class WebhookSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: UUID
    project_id: UUID
    workflow_id: str
    revision: int = Field(ge=1)
    credential_revision: int = Field(ge=1)
    key_id: str
    enabled: bool
    release_id: str
    release_digest: str
    actor_staff_id: UUID
    created_by: UUID
    updated_by: UUID
    created_at: datetime
    updated_at: datetime


class WebhookReleaseContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_id: str
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: int = Field(ge=1)


class WebhookState(BaseModel):
    webhook: WebhookSummary | None
    current_release: WebhookReleaseContext | None = None
    binding_current: bool
    registration_available: bool
    execution_available: bool = False
    reason: str | None = None


class WebhookCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret: SecretStr = Field(repr=False)

    @field_serializer("secret", when_used="json")
    def serialize_credential(self, value: SecretStr) -> str:
        # Only authenticated command replies use this DTO. Never persist it.
        return value.get_secret_value()


class WebhookReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    operation: Literal["save", "disable", "rotate"]
    webhook: WebhookSummary
    replayed: bool
    credential_status: Literal[
        "not_issued", "available", "superseded", "unavailable"
    ] = "not_issued"
    credential: WebhookCredential | None = Field(default=None, repr=False)
