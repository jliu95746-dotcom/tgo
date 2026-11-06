"""Contract for authenticated internal workflow events."""

import json
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


class WorkflowEventSourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscription_id: UUID
    revision: int = Field(ge=1, strict=True)
    delivery_id: UUID
    source_command_id: UUID
    session_id: UUID


class WorkflowEventSourceVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    workflow_id: str = Field(min_length=1, max_length=128)
    event_type: Literal["staff.session.closed"] = "staff.session.closed"
    release_id: str = Field(min_length=1, max_length=128)
    release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_binding: WorkflowEventSourceBinding


class WorkflowEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,99}$")
    data: dict[str, JsonValue] = Field(default_factory=dict)
    expected_release_id: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    expected_release_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    source_binding: WorkflowEventSourceBinding | None = None

    @field_validator("data")
    @classmethod
    def finite_json(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        json.dumps(value, allow_nan=False)
        return value

    @model_validator(mode="after")
    def complete_release_binding(self) -> Self:
        if (self.expected_release_id is None) != (
            self.expected_release_digest is None
        ):
            raise ValueError("发布记录 ID 和摘要必须同时提供。")
        if self.source_binding is not None:
            if (
                self.event_type != "staff.session.closed"
                or self.expected_release_id is None
            ):
                raise ValueError("业务来源事件必须绑定会话结束事件与完整发布版本。")
            UUID(self.project_id)
        return self


class WorkflowEventAccepted(BaseModel):
    execution_id: str
    status: str
    deduplicated: bool


class WorkflowEventTestContext(BaseModel):
    workflow_id: str
    event_type: str
    release_id: str
    release_digest: str
    version: int
    status: Literal["active"] = "active"
