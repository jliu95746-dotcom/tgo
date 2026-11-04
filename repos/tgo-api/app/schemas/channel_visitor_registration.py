"""Server channel metadata never grants browser access or business execution."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChannelVisitorRegistrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform_api_key: str = Field(min_length=1, max_length=256, repr=False)
    project_id: UUID
    platform_type: str = Field(min_length=1, max_length=50)
    platform_open_id: str = Field(min_length=1, max_length=255)
    nickname: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=255)


class ChannelVisitorRegistrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    project_id: UUID
    platform_id: UUID
    platform_open_id: str
    platform_type: str
    created_at: datetime
    updated_at: datetime
    first_visit_time: datetime
    last_visit_time: datetime
    is_online: bool
    nickname: str | None
    avatar_url: str | None
    channel_id: str
    channel_type: int
