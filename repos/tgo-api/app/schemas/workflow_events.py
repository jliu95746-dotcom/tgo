"""Event-test inputs exclude tenant overrides and signing credentials."""

import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class WorkflowEventTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    event_type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,99}$")
    expected_release_id: str = Field(min_length=1, max_length=128)
    expected_release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    data: dict[str, JsonValue]

    @field_validator("data")
    @classmethod
    def finite_json(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        json.dumps(value, allow_nan=False)
        return value


class WorkflowEventTestContext(BaseModel):
    workflow_id: str
    event_type: str
    release_id: str
    release_digest: str
    version: int
    status: Literal["active"]


class WorkflowEventAccepted(BaseModel):
    execution_id: str
    status: str
    deduplicated: bool
