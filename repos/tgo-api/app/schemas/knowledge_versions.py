"""Public knowledge version contracts; actor and embeddings are never browser inputs."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SourceKind = Literal['file', 'qa', 'website']
VersionAction = Literal['save', 'submit', 'publish']


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


class VersionResponse(BaseModel):
    id: UUID
    number: int
    state: Literal['processing', 'ready', 'pending_review', 'published', 'retired', 'failed', 'unchanged']
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
