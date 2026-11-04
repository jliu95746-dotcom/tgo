"""A script configuration record is never an activation authorization."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentDeviceScriptWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0, lt=2147483647, strict=True)
    enabled: bool = Field(default=True, strict=True)
    device_id: UUID | None = None
    expected_policy_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def exact_operation(self) -> Self:
        if self.enabled != (self.device_id is not None) or self.enabled != (
            self.expected_policy_digest is not None
        ):
            raise ValueError("Select a device to bind; omit it to disable")
        return self


class AgentDeviceScriptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "employee_device_script_configuration"
    ] = "employee_device_script_configuration"
    can_enable: Literal[False] = False
    id: UUID
    project_id: UUID
    agent_id: UUID
    device_id: UUID
    script_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    grant_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: bool = Field(strict=True)
    revision: int = Field(ge=1, le=2147483647, strict=True)
    configured_by: UUID


class AgentDeviceScriptState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    agent_id: UUID
    binding: AgentDeviceScriptRecord | None

    @model_validator(mode="after")
    def owned_binding(self) -> Self:
        if self.binding is not None and (
            self.binding.project_id != self.project_id
            or self.binding.agent_id != self.agent_id
        ):
            raise ValueError("Device script binding belongs to another scope")
        return self
