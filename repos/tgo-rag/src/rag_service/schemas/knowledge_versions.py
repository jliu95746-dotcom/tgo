"""Version contracts. Embeddings stay internal and never enter public responses."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SourceKind = Literal['file', 'qa', 'website']
VersionAction = Literal['save', 'submit', 'publish']
VersionState = Literal['processing', 'ready', 'pending_review', 'published', 'retired', 'failed', 'unchanged']


class VersionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str | None = Field(None, min_length=1)
    answer: str | None = Field(None, min_length=1)
    category: str | None = None
    subcategory: str | None = None
    tags: list[str] | None = None
    priority: int | None = Field(None, ge=0, le=100)
    replacement_file_id: UUID | None = None


class VersionChangeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: VersionInput = Field(default_factory=VersionInput)
    action: VersionAction = 'save'
    actor: str = Field(min_length=1, max_length=255)


class VersionActorRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    actor: str = Field(min_length=1, max_length=255)


class VersionChunk(BaseModel):
    content: str
    title: str | None = None
    token_count: int = 0
    content_type: str = 'paragraph'
    embedding: list[float]
    embedding_model: str


class VersionSnapshot(BaseModel):
    attempt: UUID | None = None
    website_markdown: str | None = None
    website_title: str | None = None
    content: VersionInput = Field(default_factory=VersionInput)
    chunks: list[VersionChunk] = Field(default_factory=list)
    filename: str | None = None
    storage_path: str | None = None
    file_size: int | None = None
    content_type: str | None = None


class VersionResponse(BaseModel):
    id: UUID
    number: int
    state: VersionState
    author: str
    published_by: str | None
    published_at: datetime | None
    created_at: datetime
    error: str | None
    restored_from: int | None
    content: VersionInput
    preview: str


class VersionHistoryResponse(BaseModel):
    source_kind: SourceKind
    source_id: UUID
    active_number: int | None
    disabled: bool
    retention: int
    versions: list[VersionResponse]


class VersionMaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    retention: int = Field(10, ge=2, le=100)
    confirm: bool = False
    expected_numbers: list[int] | None = None


class VersionMaintenanceResponse(BaseModel):
    removable_numbers: list[int]
    removed: int
    file_cleanup_pending: int = 0
