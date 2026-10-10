"""Validated knowledge readiness reported by the owning AI/RAG services."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .knowledge import KnowledgeChannel


class CollectionAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: str
    eligible_chunk_count: int = Field(ge=0)
    total_chunk_count: int = Field(ge=0)
    blocked_reasons: list[str] = Field(default_factory=list)


class AgentKnowledgeAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    channel: KnowledgeChannel
    agent_id: UUID
    binding_mode: Literal["project_default", "explicit", "unbound"]
    collections: list[CollectionAvailability]
    issues: list[str] = Field(default_factory=list)


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
