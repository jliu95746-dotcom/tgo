"""Public opt-in controls and private source-binding receipt."""

import json
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


class WorkflowEventSubscriptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    expected_revision: int = Field(ge=0)
    expected_release_id: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    expected_release_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def require_publication(self) -> Self:
        if self.enabled and not (
            self.expected_release_id and self.expected_release_digest
        ):
            raise ValueError("开启自动触发需要固定发布版本")
        return self


class WorkflowEventSubscriptionSummary(BaseModel):
    workflow_id: str
    subscription_id: UUID | None = None
    event_type: Literal["staff.session.closed"] = "staff.session.closed"
    enabled: bool = False
    revision: int = 0
    release_id: str | None = None
    release_digest: str | None = None
    version: int | None = None
    enabled_at: datetime | None = None


class WorkflowEventSourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription_id: UUID
    revision: int = Field(ge=1)
    delivery_id: UUID
    source_command_id: UUID
    session_id: UUID


class WorkflowSourceEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: Literal["staff.session.closed"] = "staff.session.closed"
    expected_release_id: str = Field(min_length=1, max_length=128)
    expected_release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    data: dict[str, JsonValue]
    source_binding: WorkflowEventSourceBinding

    @field_validator("data")
    @classmethod
    def finite_json(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        json.dumps(value, allow_nan=False)
        return value


class WorkflowEventSourceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    workflow_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    event_type: Literal["staff.session.closed"] = "staff.session.closed"
    release_id: str = Field(min_length=1, max_length=128)
    release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_binding: WorkflowEventSourceBinding


class WorkflowEventDeliverySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    session_id: UUID
    status: Literal["pending", "sending", "accepted", "failed", "cancelled"]
    attempts: int
    last_error: str | None
    execution_id: str | None
    created_at: datetime


class WorkflowEventDeliveryList(BaseModel):
    items: list[WorkflowEventDeliverySummary]
