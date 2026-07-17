"""Provider-neutral structured-output interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class StructuredOutputRequest(BaseModel):
    """One request for a JSON object conforming to a named schema."""

    model_config = ConfigDict(strict=True, extra="forbid")

    system_prompt: str = Field(min_length=1, max_length=4096)
    user_prompt: str = Field(min_length=1, max_length=16384)
    schema_name: str = Field(min_length=1, max_length=128)
    json_schema: str = Field(min_length=2, max_length=65536)
    repair_attempt: bool = False


@runtime_checkable
class StructuredOutputClient(Protocol):
    """Minimal adapter contract implemented by an LLM provider client."""

    async def generate(self, request: StructuredOutputRequest) -> str:
        """Return the provider's raw JSON response."""
        ...
