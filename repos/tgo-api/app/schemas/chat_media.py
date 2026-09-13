"""Immutable context carried into background and staff media processing."""

from typing import Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MediaModelOptions(TypedDict, total=False):
    disable_tools: bool


class ChatMediaInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    platform_id: UUID
    visitor_id: UUID
    source_message_id: str = Field(min_length=1, max_length=255)
    message_type: Literal[2, 4]
    reference: str = Field(min_length=1, max_length=2048)
    file_id: UUID | None = None
