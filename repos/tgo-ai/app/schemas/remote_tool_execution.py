"""Minimal untrusted remote tool request; never deserialize Python types."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool


class RemoteToolRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    tool_call_id: str = Field(min_length=1, max_length=512)
    tool_name: str = Field(min_length=1, max_length=512)
    tool_args: dict[str, JsonValue] = Field(default_factory=dict)
    requires_confirmation: StrictBool | None = None
    requires_user_input: StrictBool | None = None
