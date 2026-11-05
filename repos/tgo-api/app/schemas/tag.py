"""Tag schemas."""

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import Field, field_validator

from app.models.tag import TagCategory
from app.schemas.base import (
    BaseSchema,
    PaginatedResponse,
    SoftDeleteMixin,
    TimestampMixin,
)


# Type alias for user language
UserLanguage = Literal["zh", "en"]


def resolve_tag_display_name(
    name: str,
    name_zh: Optional[str],
    language: UserLanguage = "en",
) -> str:
    """Resolve tag display name based on user language.

    Args:
        name: Tag name (English)
        name_zh: Tag name in Chinese
        language: User's language preference ('zh' or 'en')

    Returns:
        Display name based on language preference.
        If language is 'zh' and name_zh exists, returns name_zh.
        Otherwise returns name.
    """
    if language == "zh" and name_zh:
        return name_zh
    return name


def set_tag_display_name(
    tag_response: "TagResponse",
    language: UserLanguage = "en",
) -> "TagResponse":
    """Set display_name on a TagResponse based on user language.

    Args:
        tag_response: TagResponse object to modify
        language: User's language preference

    Returns:
        TagResponse with display_name set
    """
    tag_response.display_name = resolve_tag_display_name(
        name=tag_response.name,
        name_zh=tag_response.name_zh,
        language=language,
    )
    return tag_response


def set_tag_list_display_name(
    tags: List["TagResponse"],
    language: UserLanguage = "en",
) -> List["TagResponse"]:
    """Set display_name on a list of TagResponse based on user language.

    Args:
        tags: List of TagResponse objects to modify
        language: User's language preference

    Returns:
        List of TagResponse with display_name set
    """
    for tag in tags:
        set_tag_display_name(tag, language)
    return tags


class TagBase(BaseSchema):
    """Base tag schema."""

    name: str = Field(
        ..., min_length=1, max_length=50, description="Tag name (English)"
    )
    name_zh: Optional[str] = Field(
        default=None, max_length=50, description="Tag name in Chinese"
    )
    category: TagCategory = Field(..., description="Tag category")
    weight: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Tag importance/priority weight (0-10)",
    )
    color: Optional[str] = Field(
        default=None, max_length=20, description="Tag color"
    )
    description: Optional[str] = Field(
        default=None, max_length=255, description="Tag description"
    )


class TagCreate(TagBase):
    """Schema for creating a tag."""

    pass


class TagUpdate(BaseSchema):
    """Schema for updating a tag."""

    name_zh: Optional[str] = Field(
        default=None, max_length=50, description="Updated tag name in Chinese"
    )
    weight: Optional[int] = Field(
        default=None, ge=0, le=10, description="Updated tag weight"
    )
    color: Optional[str] = Field(
        default=None, max_length=20, description="Updated tag color"
    )
    description: Optional[str] = Field(
        default=None, max_length=255, description="Updated tag description"
    )

    @field_validator("color")
    @classmethod
    def validate_color(cls, v: Optional[str]) -> Optional[str]:
        """Validate color format (hex color)."""
        if v is None:
            return v
        if not v.startswith("#") or len(v) not in [4, 7]:
            raise ValueError(
                "Color must be a valid hex color (e.g., #FF5733 or #F53)"
            )
        return v


class TagInDB(TagBase, TimestampMixin, SoftDeleteMixin):
    """Schema for tag in database."""

    id: str = Field(..., description="Base64 encoded tag ID")
    project_id: UUID = Field(..., description="Associated project ID")


class TagResponse(TagInDB):
    """Schema for tag response."""

    display_name: Optional[str] = Field(
        default=None,
        description=(
            'Display name based on user language (name_zh for Chinese, name '
            'for others)'
        ),
    )


class TagListParams(BaseSchema):
    """Parameters for listing tags."""

    category: Optional[TagCategory] = Field(
        default=None, description="Filter tags by category"
    )
    search: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Search tags by name",
    )
    limit: int = Field(
        default=50, ge=1, le=100, description="Number of tags to return"
    )
    offset: int = Field(default=0, ge=0, description="Number of tags to skip")


class TagListResponse(PaginatedResponse):
    """Schema for tag list response."""

    data: list[TagResponse] = Field(..., description="List of tags")


class VisitorTagCreate(BaseSchema):
    """Schema for creating a visitor-tag relationship."""

    visitor_id: UUID = Field(..., description="Visitor ID")
    tag_id: str = Field(..., description="Tag ID (Base64 encoded)")


class VisitorTagResponse(BaseSchema):
    """Schema for visitor-tag relationship response."""

    id: UUID = Field(..., description="Visitor-tag relationship ID")
    project_id: UUID = Field(..., description="Associated project ID")
    visitor_id: UUID = Field(..., description="Associated visitor ID")
    tag_id: str = Field(..., description="Associated tag ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    deleted_at: Optional[datetime] = Field(
        default=None, description="Soft deletion timestamp"
    )
