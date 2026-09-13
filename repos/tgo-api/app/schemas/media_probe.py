"""Public result of a saved-model test; no credentials or provider raw errors."""
from pydantic import BaseModel, Field


class MediaProbeResult(BaseModel):
    success: bool
    message: str = Field(max_length=512)
    error_code: str | None = None
    output: str | None = Field(default=None, max_length=65535)
