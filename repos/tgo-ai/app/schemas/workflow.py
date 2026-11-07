"""Workflow-related Pydantic schemas."""

from typing import List, Optional

from pydantic import Field

from app.schemas.base import BaseSchema


class WorkflowResponse(BaseSchema):
    """Schema for workflow API responses from Workflow service."""

    id: str = Field(description="Workflow ID")
    name: str = Field(description="Workflow name")
    description: Optional[str] = Field(
        default=None, description="Workflow description"
    )
    tags: List[str] = Field(default_factory=list, description="Workflow tags")
    status: str = Field(description="Current status")
    version: int = Field(description="Version number")
    updated_at: Optional[str] = Field(
        description="Last update time, null if details are unavailable"
    )
    enabled: bool = Field(
        default=True,
        description=(
            'Whether this workflow is enabled for the current agent binding'
        ),
    )
    bound_release_version: Optional[int] = None
    bound_release_id: Optional[str] = None
    bound_release_digest: Optional[str] = None
    details_available: bool = Field(
        default=True,
        description="Whether the workflow owner returned current details",
    )
