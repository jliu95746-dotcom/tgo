"""Knowledge readiness and audited edits to channel permissions."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .knowledge_governance import KnowledgeChannel


class CollectionAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: str
    eligible_chunk_count: int = Field(ge=0)
    total_chunk_count: int = Field(ge=0)
    blocked_reasons: list[str] = Field(default_factory=list)


class KnowledgeAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    channel: KnowledgeChannel
    collections: list[CollectionAvailability]


class ChannelUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channels: list[KnowledgeChannel] = Field(min_length=1, max_length=5)
    expected_updated_at: datetime

    @model_validator(mode="after")
    def valid_edit(self) -> "ChannelUpdateRequest":
        if len(self.channels) != len(set(self.channels)):
            raise ValueError("channels must not contain duplicates")
        if self.expected_updated_at.utcoffset() is None:
            raise ValueError("expected_updated_at must be timezone-aware")
        return self


class ChannelUpdateDecision(ChannelUpdateRequest):
    reviewer: str = Field(min_length=1, max_length=255)
