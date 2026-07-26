"""Skill schemas for tgo-api gateway (transparent proxy to tgo-ai)."""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import Field

from app.schemas.base import BaseSchema


# ---------------------------------------------------------------------------
# Response Schemas (mirror of tgo-ai schemas)
# ---------------------------------------------------------------------------

class SkillSummary(BaseSchema):
    """Lightweight summary for listing skills."""

    name: str = Field(description="Skill identifier (= directory name)")
    description: str = Field(description="Short description of the skill")
    author: Optional[str] = Field(default=None, description="Skill author")
    is_official: bool = Field(default=False, description="Whether this is an official skill")
    is_featured: bool = Field(default=False, description="Whether this skill is featured")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")
    updated_at: Optional[datetime] = Field(default=None, description="Last modification time")
    enabled: bool = Field(default=True, description="Whether this skill is enabled for the project")
    skill_type: Literal["standard", "humanization"] = Field(
        default="standard", description="Skill category"
    )
    display_name: Optional[str] = Field(default=None, description="Human-facing name")
    pending_training_count: int = Field(default=0, ge=0)
    published_version: int = Field(default=1, ge=1)


class SkillDetail(SkillSummary):
    """Full skill detail including instructions and file listings."""

    instructions: str = Field(description="Markdown body of SKILL.md")
    license: Optional[str] = Field(default=None, description="License identifier")
    version: Optional[str] = Field(default=None, description="Skill version")
    metadata: Optional[Dict[str, str]] = Field(default=None, description="Additional metadata")
    scripts: List[str] = Field(default_factory=list, description="Script file paths")
    references: List[str] = Field(default_factory=list, description="Reference file paths")


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class SkillCreateRequest(BaseSchema):
    """Schema for creating a new skill."""

    name: str = Field(
        ...,
        max_length=64,
        pattern=r"^[a-z][a-z0-9-]*[a-z0-9]$",
        description="Skill name (lowercase + digits + hyphens)",
    )
    description: str = Field(..., max_length=1024, description="Short description")
    instructions: Optional[str] = Field(default=None, description="SKILL.md markdown body")
    author: Optional[str] = Field(default=None, description="Skill author name")
    license: Optional[str] = Field(default=None, description="License identifier")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")
    is_featured: bool = Field(default=False, description="Whether to feature this skill")
    metadata: Optional[Dict[str, str]] = Field(default=None, description="Additional metadata")
    scripts: Optional[Dict[str, str]] = Field(default=None, description="Script files {filename: content}")
    references: Optional[Dict[str, str]] = Field(default=None, description="Reference files {filename: content}")


class SkillImportRequest(BaseSchema):
    """Schema for importing a skill from a GitHub directory URL."""

    github_url: str = Field(
        ...,
        description="GitHub directory URL (e.g. https://github.com/owner/repo/tree/main/skills/my-skill)",
    )
    github_token: Optional[str] = Field(
        default=None,
        description="Optional GitHub personal access token for private repos or higher rate limits",
    )


class SkillToggleRequest(BaseSchema):
    """Schema for toggling a skill's enabled/disabled state."""

    enabled: bool = Field(..., description="Whether the skill should be enabled")


class SkillToggleResponse(BaseSchema):
    """Response for a skill toggle operation."""

    name: str = Field(description="Skill name")
    enabled: bool = Field(description="New enabled state")


class SkillUpdateRequest(BaseSchema):
    """Schema for updating an existing skill."""

    description: Optional[str] = Field(default=None, max_length=1024, description="Updated description")
    instructions: Optional[str] = Field(default=None, description="Updated SKILL.md markdown body")
    author: Optional[str] = Field(default=None, description="Updated author name")
    license: Optional[str] = Field(default=None, description="Updated license identifier")
    tags: Optional[List[str]] = Field(default=None, description="Updated tags")
    is_featured: Optional[bool] = Field(default=None, description="Updated featured status")
    metadata: Optional[Dict[str, str]] = Field(default=None, description="Updated metadata")


class HumanizationSkillCreateRequest(BaseSchema):
    name: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
    )
    display_name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=1024)


class HumanizationTrainingSampleRequest(BaseSchema):
    customer_message: str = Field(..., min_length=1, max_length=10000)
    ai_draft: str = Field(..., min_length=1, max_length=10000)
    final_reply: str = Field(..., min_length=1, max_length=10000)
    source_message_id: Optional[str] = Field(default=None, max_length=255)


class HumanizationTrainingStatus(BaseSchema):
    name: str
    pending_training_count: int = Field(ge=0)
    published_version: int = Field(ge=1)


class HumanizationTrainingApplyResponse(HumanizationTrainingStatus):
    applied_count: int = Field(ge=0)
