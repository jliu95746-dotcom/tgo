"Verified channel services resolve their own platform's external visitor \
ID."

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, TGOAPIException
from app.models.platform import DIRECT_VISITOR_PLATFORM_TYPES, Platform
from app.models.project import Project
from app.models.visitor import Visitor
from app.schemas.channel_visitor_registration import (
    ChannelVisitorRegistrationInput,
    ChannelVisitorRegistrationResult,
)
from app.services import visitor_service
from app.services.visitor_access import current_visitor
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


async def register_channel_visitor(
    db: Session, payload: ChannelVisitorRegistrationInput
) -> ChannelVisitorRegistrationResult:
    platform: Platform | None = db.scalar(
        select(Platform)
        .join(Project, Project.id == Platform.project_id)
        .where(
            Platform.api_key == payload.platform_api_key,
            Platform.project_id == payload.project_id,
            Platform.type == payload.platform_type,
            ~Platform.type.in_(DIRECT_VISITOR_PLATFORM_TYPES),
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
            Project.deleted_at.is_(None),
        )
        .with_for_update(of=Platform)
        .execution_options(populate_existing=True)
    )
    if platform is None:
        raise TGOAPIException(
            "内部渠道资源或项目范围无效", code="INVALID_CHANNEL_SCOPE", status_code=403
        )
    rows = list(
        db.scalars(
            select(Visitor)
            .where(
                Visitor.project_id == platform.project_id,
                Visitor.platform_id == platform.id,
                Visitor.platform_open_id == payload.platform_open_id,
                Visitor.deleted_at.is_(None),
            )
            .limit(2)
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) > 1:
        raise ConflictError("旧渠道访客存在重复记录，需要核对后继续")
    if rows:
        visitor = rows[0]
        if (
            visitor.access_retired_at is not None
            or visitor.access_secret_digest is not None
        ):
            raise ConflictError("此渠道访客存在已结束或浏览器访问记录，需要核对后继续")
        if payload.nickname is not None:
            (
                visitor.nickname,
                visitor.nickname_zh,
            ) = visitor_service.resolve_visitor_nickname(
                payload.nickname, payload.nickname, payload.platform_open_id
            )
        if payload.avatar_url is not None:
            visitor.avatar_url = payload.avatar_url
        visitor.last_visit_time = datetime.utcnow()
        db.commit()
        await visitor_service.ensure_visitor_channel(db, visitor, platform)
    else:
        visitor = await visitor_service.create_visitor_with_channel(
            db=db,
            platform=platform,
            platform_open_id=payload.platform_open_id,
            nickname=payload.nickname,
            nickname_zh=payload.nickname,
            avatar_url=payload.avatar_url,
        )
    visitor = current_visitor(db, visitor)
    current_platform = db.get(Platform, platform.id, populate_existing=True)
    if (
        current_platform is None
        or current_platform.api_key != payload.platform_api_key
        or current_platform.type != payload.platform_type
    ):
        raise TGOAPIException(
            "内部渠道资源已改变", code="INVALID_CHANNEL_SCOPE", status_code=403
        )
    db.commit()
    return ChannelVisitorRegistrationResult(
        id=visitor.id,
        project_id=visitor.project_id,
        platform_id=visitor.platform_id,
        platform_open_id=visitor.platform_open_id,
        platform_type=platform.type,
        created_at=visitor.created_at,
        updated_at=visitor.updated_at,
        first_visit_time=visitor.first_visit_time,
        last_visit_time=visitor.last_visit_time,
        is_online=visitor.is_online,
        nickname=visitor.nickname,
        avatar_url=visitor.avatar_url,
        channel_id=build_visitor_channel_id(visitor.id),
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
    )
