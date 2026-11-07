"""Secret-free resources shared by business setup."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.knowledge import KnowledgeChannel
from app.schemas.knowledge_availability import CollectionAvailability

ResourceKind = Literal[
    "model", "knowledge", "tool", "workflow", "connection", "device"
]
ResourceStatus = Literal[
    "configured", "not_ready", "offline", "disabled", "unknown"
]


class ResourceDetails(BaseModel):
    provider_id: UUID | None = None
    model_id: str | None = None
    purpose: str | None = None
    is_default: bool = False
    eligible_chunk_count: int | None = Field(default=None, ge=0)
    total_chunk_count: int | None = Field(default=None, ge=0)
    blocked_reasons: list[str] = Field(default_factory=list)
    workflow_version: int | None = None
    resource_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class ScenarioResource(BaseModel):
    id: str
    kind: ResourceKind
    name: str
    status: ResourceStatus
    details: ResourceDetails = Field(default_factory=ResourceDetails)


class ResourceSection(BaseModel):
    kind: ResourceKind
    status: Literal[
        "available", "partial", "unavailable", "not_supported"
    ] = "available"
    items: list[ScenarioResource] = Field(default_factory=list)
    message: str | None = None


class ScenarioResourceCatalog(BaseModel):
    project_id: UUID
    channel: KnowledgeChannel
    checked_at: datetime
    sections: list[ResourceSection]


class KnowledgeAvailability(BaseModel):
    project_id: UUID
    channel: KnowledgeChannel
    collections: list[CollectionAvailability]


class ScopedToolRow(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    deleted_at: datetime | None = None


class ScopedWorkflowRow(BaseModel):
    published_version: int | None = None
    id: str
    project_id: UUID
    name: str
    status: Literal["draft", "active", "archived"]
    version: int


class CatalogPagination(BaseModel):
    has_next: bool


class ScopedWorkflowPage(BaseModel):
    data: list[ScopedWorkflowRow]
    pagination: CatalogPagination


class ScopedDeviceRow(BaseModel):
    id: UUID
    project_id: UUID
    device_name: str
    status: Literal["online", "offline"]


class ScopedDevicePage(BaseModel):
    devices: list[ScopedDeviceRow]
    total: int = Field(ge=0)
