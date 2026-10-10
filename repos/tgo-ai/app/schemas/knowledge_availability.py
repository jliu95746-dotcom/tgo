"""Tenant-scoped availability contracts shared with RAG and the gateway."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .knowledge import KnowledgeChannel


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


class AgentKnowledgeAvailability(KnowledgeAvailability):
    agent_id: UUID
    binding_mode: Literal["project_default", "explicit", "unbound"]
    issues: list[str] = Field(default_factory=list)
