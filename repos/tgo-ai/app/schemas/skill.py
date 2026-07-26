"""Skill-related Pydantic schemas for file-system-based skill management."""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import Field

from app.schemas.base import BaseSchema


# ---------------------------------------------------------------------------
# Response Schemas (parsed from file system)
# ---------------------------------------------------------------------------

class SkillSummary(BaseSchema):
    """Lightweight summary for listing skills (parsed from SKILL.md frontmatter)."""

    name: str = Field(
        description="Skill identifier (= directory name)",
        examples=["code-review"],
    )
    description: str = Field(
        description="Short description of the skill",
        examples=["Code review with style checking and best practices."],
    )
    author: Optional[str] = Field(
        default=None,
        description="Skill author",
        examples=["tgo-official"],
    )
    is_official: bool = Field(
        default=False,
        description="Whether this is an official/built-in skill (from _official/ directory)",
    )
    is_featured: bool = Field(
        default=False,
        description="Whether this skill is featured/recommended",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Tags for categorization and filtering",
        examples=[["code", "review", "python"]],
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Last modification time of SKILL.md",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this skill is enabled for the project",
    )
    skill_type: Literal["standard", "humanization"] = Field(
        default="standard",
        description="Skill category used by the management UI and runtime",
    )
    display_name: Optional[str] = Field(
        default=None,
        description="Human-facing name; may contain Chinese characters",
    )
    pending_training_count: int = Field(
        default=0,
        ge=0,
        description="Humanization corrections waiting for manual publication",
    )
    published_version: int = Field(
        default=1,
        ge=1,
        description="Published humanization training version",
    )


class SkillDetail(SkillSummary):
    """Full skill detail including instructions and file listings."""

    instructions: str = Field(
        description="Markdown body of SKILL.md (the actual skill instructions)",
        examples=["# Code Review Skill\n\n## When to use this skill\n..."],
    )
    license: Optional[str] = Field(
        default=None,
        description="License identifier",
        examples=["Apache-2.0"],
    )
    version: Optional[str] = Field(
        default=None,
        description="Skill version",
        examples=["1.0.0"],
    )
    metadata: Optional[Dict[str, str]] = Field(
        default=None,
        description="Additional metadata key-value pairs",
    )
    scripts: List[str] = Field(
        default_factory=list,
        description="List of script file paths within the skill directory",
        examples=[["scripts/check_style.py"]],
    )
    references: List[str] = Field(
        default_factory=list,
        description="List of reference file paths within the skill directory",
        examples=[["references/style-guide.md"]],
    )


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class SkillCreateRequest(BaseSchema):
    """Schema for creating a new skill directory with SKILL.md."""

    name: str = Field(
        ...,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        description="Skill name (lowercase + digits + hyphens, no consecutive hyphens)",
        examples=["code-review", "1password"],
    )
    description: str = Field(
        ...,
        max_length=1024,
        description="Short description of the skill",
    )
    instructions: Optional[str] = Field(
        default=None,
        description="SKILL.md markdown body (instructions for the AI agent)",
    )
    author: Optional[str] = Field(
        default=None,
        description="Skill author name",
    )
    license: Optional[str] = Field(
        default=None,
        description="License identifier",
        examples=["Apache-2.0", "MIT"],
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Tags for categorization",
    )
    is_featured: bool = Field(
        default=False,
        description="Whether this skill should be featured",
    )
    metadata: Optional[Dict[str, str]] = Field(
        default=None,
        description="Additional metadata key-value pairs",
    )
    scripts: Optional[Dict[str, str]] = Field(
        default=None,
        description="Script files to create: {filename: content}",
        examples=[{"check_style.py": "#!/usr/bin/env python3\n..."}],
    )
    references: Optional[Dict[str, str]] = Field(
        default=None,
        description="Reference files to create: {filename: content}",
        examples=[{"style-guide.md": "# Style Guide\n..."}],
    )


class SkillImportRequest(BaseSchema):
    """Schema for importing a skill from a GitHub directory URL."""

    github_url: str = Field(
        ...,
        description="GitHub directory URL (e.g. https://github.com/owner/repo/tree/main/skills/my-skill)",
        examples=["https://github.com/anthropics/skills/tree/main/skills/code-review"],
    )
    github_token: Optional[str] = Field(
        default=None,
        description="Optional GitHub personal access token for private repos or higher rate limits",
    )


class SkillToggleRequest(BaseSchema):
    """Schema for toggling a skill's enabled/disabled state."""

    enabled: bool = Field(
        ...,
        description="Whether the skill should be enabled (true) or disabled (false)",
    )


class SkillToggleResponse(BaseSchema):
    """Response for a skill toggle operation."""

    name: str = Field(description="Skill name")
    enabled: bool = Field(description="New enabled state")


class SkillUpdateRequest(BaseSchema):
    """Schema for updating an existing skill's SKILL.md content."""

    description: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Updated description",
    )
    instructions: Optional[str] = Field(
        default=None,
        description="Updated SKILL.md markdown body",
    )
    author: Optional[str] = Field(
        default=None,
        description="Updated author name",
    )
    license: Optional[str] = Field(
        default=None,
        description="Updated license identifier",
    )
    tags: Optional[List[str]] = Field(
        default=None,
        description="Updated tags (replaces existing)",
    )
    is_featured: Optional[bool] = Field(
        default=None,
        description="Updated featured status",
    )
    metadata: Optional[Dict[str, str]] = Field(
        default=None,
        description="Updated metadata (replaces existing)",
    )


class HumanizationSkillCreateRequest(BaseSchema):
    """Create a trainable humanization skill with a stable ASCII slug."""

    name: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        description="Optional ASCII slug; generated when omitted",
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-facing name; Chinese is supported",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Purpose and target customer-service scenarios",
    )


class HumanizationTrainingSampleRequest(BaseSchema):
    """A staff-approved correction captured from assist mode."""

    customer_message: str = Field(..., min_length=1, max_length=10000)
    ai_draft: str = Field(..., min_length=1, max_length=10000)
    final_reply: str = Field(..., min_length=1, max_length=10000)
    source_message_id: Optional[str] = Field(default=None, max_length=255)


class HumanizationTrainingStatus(BaseSchema):
    """Pending and published state for one humanization skill."""

    name: str
    pending_training_count: int = Field(ge=0)
    published_version: int = Field(ge=1)


class HumanizationTrainingApplyResponse(HumanizationTrainingStatus):
    """Result of manually publishing pending correction samples."""

    applied_count: int = Field(ge=0)
