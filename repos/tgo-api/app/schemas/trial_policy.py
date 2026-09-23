"""Versioned settings for future trial grants, separate from sold plans."""

from pydantic import BaseModel, ConfigDict, Field


class TrialPolicyResponse(BaseModel):
    version: int
    ai_replies: int
    days: int
    seats: int


class TrialPolicyChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    ai_replies: int = Field(ge=0, le=100000)
    reason: str = Field(min_length=5, max_length=500)
