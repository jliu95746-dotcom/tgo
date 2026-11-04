"""Explicit command and receipt for a legacy employee workflow release pin."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import PaginationMetadata


class WorkflowPinTransitionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_binding_id: UUID
    release_version: int = Field(strict=True, ge=1)
    confirmed_disabled: Literal[True]


class WorkflowPinTransitionReceipt(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    project_id: UUID
    requested_by: UUID
    request_id: UUID
    agent_id: UUID
    binding_id: UUID
    workflow_id: str
    old_release_version: int | None = None
    old_release_id: str | None = None
    old_release_digest: str | None = None
    release_version: int
    release_id: str
    release_digest: str
    agent_active: bool
    binding_enabled: Literal[False]
    created_at: datetime


class WorkflowPinTransitionHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[WorkflowPinTransitionReceipt]
    pagination: PaginationMetadata
