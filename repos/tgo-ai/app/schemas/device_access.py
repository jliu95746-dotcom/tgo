"""Minimum device identity needed to validate an AI agent binding."""

from uuid import UUID
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BoundDeviceIdentity(BaseModel):
    id: UUID
    project_id: UUID


class BoundDeviceReadPolicy(BaseModel):
    device_id: UUID
    project_id: UUID
    tool_name: Literal["fs_read"]
    status: Literal["active", "pending", "revoked"]
    verified: bool = Field(strict=True)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


AgentDeviceActionStatus = Literal[
    "not_bound", "ready", "needs_rebind", "needs_test",
    "legacy_unverified", "device_missing", "invalid_binding",
]


class AgentDeviceActionReadiness(BaseModel):
    """Current read action status; this is not an execution credential."""

    agent_id: UUID
    device_id: str | None = None
    tool_name: Literal["fs_read"] = "fs_read"
    status: AgentDeviceActionStatus
    policy_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_ready_identity(self) -> "AgentDeviceActionReadiness":
        if self.status == "ready":
            if self.device_id is None or self.policy_digest is None:
                raise ValueError("Ready device action requires a device ID and policy digest")
            UUID(self.device_id)
        elif self.status == "not_bound" and self.device_id is not None:
            raise ValueError("Unbound action cannot carry a device ID")
        elif self.policy_digest is not None:
            raise ValueError("Unready device action cannot carry a policy digest")
        return self
