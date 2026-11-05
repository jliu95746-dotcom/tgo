"""Project schemas."""

from typing import Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema, SoftDeleteMixin, TimestampMixin


class ProjectBase(BaseSchema):
    """Base project schema."""

    name: str = Field(
        ..., min_length=1, max_length=255, description="Project name"
    )


class ProjectCreate(ProjectBase):
    """Schema for creating a project."""

    pass


class ProjectUpdate(BaseSchema):
    """Schema for updating a project."""

    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated project name",
    )


class ProjectInDB(ProjectBase, TimestampMixin, SoftDeleteMixin):
    """Schema for project in database."""

    id: UUID = Field(..., description="Project ID")
    api_key: str = Field(..., description="API key for authentication")


class ProjectResponse(ProjectInDB):
    """Schema for project response."""

    pass


class ProjectListResponse(BaseSchema):
    """Schema for project list response."""

    data: list[ProjectResponse] = Field(..., description="List of projects")
