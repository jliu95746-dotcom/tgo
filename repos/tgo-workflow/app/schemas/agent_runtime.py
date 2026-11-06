"""The subset of the current AI single-agent contract used by workflows."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class AgentRuntimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID
    message: str = Field(min_length=1, max_length=10000)
    stream: Literal[False] = False
    knowledge_channel: Literal["internal"] = "internal"
    require_current_knowledge: Literal[True] = True
    session_id: str | None = Field(default=None, max_length=255)
    expected_device_id: UUID | None = None
    expected_device_policy_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def require_complete_device_pin(self) -> "AgentRuntimeRequest":
        if (self.expected_device_id is None) != (
            self.expected_device_policy_digest is None
        ):
            raise ValueError(
                "Device policy pin requires both device ID and digest"
            )
        return self


class AgentRuntimeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    success: StrictBool
    content: str
    execution_id: UUID | None = None

    @model_validator(mode="after")
    def successful_answer_requires_text(self) -> "AgentRuntimeResult":
        if self.success and not self.content.strip():
            raise ValueError("Successful agent result requires content")
        if self.success and self.execution_id is None:
            raise ValueError("Successful agent result requires execution_id")
        return self
