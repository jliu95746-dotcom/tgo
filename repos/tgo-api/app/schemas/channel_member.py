"""Channel member schemas."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema, TimestampMixin, SoftDeleteMixin


MemberType = Literal["visitor", "staff"]


class ChannelMemberBase(BaseSchema):
    """Base fields for channel membership."""

    project_id: UUID = Field(..., description="Associated project ID")
    channel_id: str = Field(
        ..., description="WuKongIM channel ID (Base62-encoded)"
    )
    channel_type: int = Field(
        ..., description="WuKongIM channel type (e.g., 251)"
    )
    member_id: UUID = Field(
        ..., description="Member UUID (Visitor.id or Staff.id)"
    )
    member_type: MemberType = Field(
        ..., description="Member type: visitor or staff"
    )


class ChannelMemberCreate(ChannelMemberBase):
    """Schema for creating a channel member."""

    pass


class ChannelMemberInDB(ChannelMemberBase, TimestampMixin, SoftDeleteMixin):
    """Channel member as stored in DB."""

    id: UUID = Field(..., description="Primary key")


class ChannelMemberResponse(ChannelMemberInDB):
    """Response schema for channel member."""

    pass
