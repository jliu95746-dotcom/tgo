"""Reuse current realtime ownership for website history and attachment operations."""

import hmac
from uuid import UUID

from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.models import Platform, Project, Visitor
from app.models.platform import DIRECT_VISITOR_PLATFORM_TYPES, PlatformType
from app.schemas.visitor_data_access import VisitorDataAccess
from app.services.im_access import authenticate_im_actor, read_visitor_im_access
from app.services.visitor_access import denied
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import parse_visitor_channel_id
from app.utils.wechat_local import is_filehelper_identity


def _current_platform(db: Session, platform_id: UUID) -> Platform:
    platform: Platform | None = (
        db.query(Platform)
        .join(Project, Platform.project_id == Project.id)
        .filter(
            Platform.id == platform_id,
            Platform.type.in_(DIRECT_VISITOR_PLATFORM_TYPES),
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
            Project.deleted_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if platform is None:
        raise denied()
    return platform


def require_visitor_data_access(
    db: Session,
    platform: Platform,
    credentials: HTTPAuthorizationCredentials | None,
) -> VisitorDataAccess:
    """Only a signed visitor IM grant with current scope authorizes website data."""
    if (
        not isinstance(credentials, HTTPAuthorizationCredentials)
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials
        or len(credentials.credentials) > 8192
    ):
        raise denied()
    token = credentials.credentials
    claims = read_visitor_im_access(token)
    actor = authenticate_im_actor(db, f"{claims.sub}-vtr", token)
    if (
        not isinstance(actor, Visitor)
        or actor.platform_id != platform.id
        or actor.project_id != platform.project_id
        or (
            platform.type == PlatformType.WECHAT_LOCAL.value
            and not is_filehelper_identity(actor.platform_open_id)
        )
    ):
        raise denied()
    return VisitorDataAccess(
        visitor_id=actor.id,
        project_id=actor.project_id,
        platform_id=actor.platform_id,
        platform_api_key=SecretStr(platform.api_key),
        im_token=SecretStr(token),
    )


def reauthorize_visitor_data(db: Session, access: VisitorDataAccess) -> Visitor:
    """Check the original proof again after an await, without adopting a new owner."""
    platform = _current_platform(db, access.platform_id)
    if (
        platform.project_id != access.project_id
        or not hmac.compare_digest(
            platform.api_key, access.platform_api_key.get_secret_value()
        )
    ):
        raise denied()
    actor = authenticate_im_actor(
        db, f"{access.visitor_id}-vtr", access.im_token.get_secret_value()
    )
    if (
        not isinstance(actor, Visitor)
        or actor.id != access.visitor_id
        or actor.platform_id != access.platform_id
        or actor.project_id != access.project_id
        or (
            platform.type == PlatformType.WECHAT_LOCAL.value
            and not is_filehelper_identity(actor.platform_open_id)
        )
    ):
        raise denied()
    return actor


def authorize_visitor_data_channel(
    access: VisitorDataAccess, channel_id: str, channel_type: int
) -> None:
    authorize_visitor_channel_id(access.visitor_id, channel_id, channel_type)


def authorize_visitor_channel_id(visitor_id: UUID, channel_id: str, channel_type: int) -> None:
    if channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE:
        try:
            requested_id = parse_visitor_channel_id(channel_id)
        except ValueError:
            raise denied() from None
    elif channel_type == 1:
        try:
            requested_id = UUID(channel_id.removesuffix("-vtr"))
        except ValueError:
            raise denied() from None
    else:
        raise denied()
    if requested_id != visitor_id:
        raise denied()
