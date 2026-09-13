"""Shared authorization and routing for staff outbound messages."""

from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, joinedload

from app.models import ChannelMember, Staff, Visitor
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE, MEMBER_TYPE_STAFF
from app.utils.encoding import parse_visitor_channel_id


class PlatformMessageTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: UUID
    visitor_id: UUID
    platform_id: UUID
    platform_type: str
    platform_api_key: str = Field(repr=False)
    platform_open_id: str
    channel_id: str
    channel_type: int

    @property
    def requires_external_delivery(self) -> bool:
        return (
            self.platform_type != "website" and self.platform_open_id != self.channel_id
        )


class StaffMessageTarget(PlatformMessageTarget):
    agent_id: str | None = None
    staff_id: UUID
    service_mode: str | None = None
    humanization_skill_name: str | None = None
    humanization_skill_enabled: bool = False


def resolve_staff_message_target(
    db: Session, user: Staff, channel_id: str, channel_type: int
) -> StaffMessageTarget:
    if channel_type != CHANNEL_TYPE_CUSTOMER_SERVICE:
        raise HTTPException(
            400, "Only customer service channels (type 251) are supported"
        )
    try:
        visitor_uuid = parse_visitor_channel_id(channel_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid channel_id format") from exc
    membership = (
        db.query(ChannelMember)
        .filter(
            ChannelMember.project_id == user.project_id,
            ChannelMember.channel_id == channel_id,
            ChannelMember.channel_type == channel_type,
            ChannelMember.member_id == user.id,
            ChannelMember.member_type == MEMBER_TYPE_STAFF,
            ChannelMember.deleted_at.is_(None),
        )
        .first()
    )
    if not membership:
        raise HTTPException(403, "Staff not assigned to this channel")
    visitor = (
        db.query(Visitor)
        .options(joinedload(Visitor.platform))
        .filter(
            Visitor.id == visitor_uuid,
            Visitor.project_id == user.project_id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )
    if not visitor:
        raise HTTPException(404, "Visitor not found")
    platform = visitor.platform
    if not platform or platform.deleted_at is not None:
        raise HTTPException(400, "Visitor platform is unavailable")
    if not platform.is_active:
        raise HTTPException(403, "Visitor platform is disabled")
    if not platform.api_key:
        raise HTTPException(400, "Platform API key is missing")
    if platform.project_id != user.project_id:
        raise HTTPException(403, "Access denied for visitor platform")
    return StaffMessageTarget(
        project_id=user.project_id,
        staff_id=user.id,
        agent_id=str(platform.agent_id) if platform.agent_id else None,
        visitor_id=visitor.id,
        platform_id=platform.id,
        platform_type=platform.type,
        platform_api_key=platform.api_key,
        platform_open_id=visitor.platform_open_id,
        channel_id=channel_id,
        channel_type=channel_type,
        service_mode=getattr(visitor, "service_mode", None),
        humanization_skill_name=getattr(visitor, "humanization_skill_name", None),
        humanization_skill_enabled=getattr(
            visitor, "humanization_skill_enabled", False
        ),
    )
