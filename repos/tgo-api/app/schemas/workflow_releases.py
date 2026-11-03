"""Workflow publication proxy contracts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WorkflowPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_draft_version: int = Field(ge=1)
    expected_publication_revision: int = Field(ge=0)


class WorkflowRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_version: int = Field(ge=1)
    expected_publication_revision: int = Field(ge=0)


class WorkflowReleaseSummary(BaseModel):
    id: str
    version: int
    draft_version: int
    created_by: str | None
    created_at: datetime
