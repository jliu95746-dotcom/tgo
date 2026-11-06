"""Original staff/visitor authority and visible peers for profile operations."""

from dataclasses import dataclass
from collections.abc import Callable
from uuid import UUID
from typing import TypeAlias

from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.security import resolve_staff_token
from app.models import (
    ChannelMember,
    Platform,
    Project,
    Staff,
    Visitor,
    VisitorSession,
)
from app.schemas.staff_data_access import StaffDataAccess
from app.schemas.visitor_data_access import VisitorDataAccess
from app.services import visitor_chat_access
from app.services.staff_conversation_scope import require_owned_visitor
from app.services.visitor_access import denied
from app.services.visitor_data_access import (
    require_visitor_data_access,
    reauthorize_visitor_data,
    authorize_visitor_channel_id,
)
from app.core.exceptions import TGOAPIException
from app.utils.const import MEMBER_TYPE_STAFF
from app.utils.encoding import (
    build_visitor_channel_id,
    parse_visitor_channel_id,
)

ProfileAccess: TypeAlias = StaffDataAccess | VisitorDataAccess


@dataclass(frozen=True)
class OperationGuard:
    check: Callable[[], None]


def not_found() -> TGOAPIException:
    return TGOAPIException("资料不存在或无权访问", "NOT_FOUND", status_code=404)


def require_profile_access(
    db: Session,
    credentials: HTTPAuthorizationCredentials | None,
    platform_key: str | None,
) -> ProfileAccess:
    if (
        not isinstance(credentials, HTTPAuthorizationCredentials)
        or credentials.scheme.lower() != "bearer"
    ):
        raise denied()
    token = credentials.credentials
    if not token or len(token) > 8192:
        raise denied()
    staff = resolve_staff_token(db, token)
    if staff is not None:
        access = StaffDataAccess(
            staff_id=staff.id,
            project_id=staff.project_id,
            username=staff.username,
            role=staff.role,
            token_version=staff.token_version,
            token=SecretStr(token),
        )
        check_profile_access(db, access)
        return access
    if not platform_key:
        raise denied()
    platform = (
        db.query(Platform)
        .join(Project, Platform.project_id == Project.id)
        .filter(
            Platform.api_key == platform_key,
            Platform.type == "website",
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
            Project.deleted_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if platform is None:
        raise denied()
    return require_visitor_data_access(db, platform, credentials)


def check_profile_access(
    db: Session, access: ProfileAccess
) -> Staff | Visitor:
    if isinstance(access, VisitorDataAccess):
        return reauthorize_visitor_data(db, access)
    # Refresh the original member before resolving its original token again.
    staff = (
        db.query(Staff)
        .filter(Staff.id == access.staff_id)
        .populate_existing()
        .first()
    )
    actor: Staff | None = resolve_staff_token(
        db, access.token.get_secret_value()
    )
    if (
        staff is None
        or actor is None
        or actor.id != access.staff_id
        or actor.project_id != access.project_id
        or actor.username != access.username
        or actor.token_version != access.token_version
        or actor.role != access.role
    ):
        raise denied()
    return actor


def require_profile_visitor(
    db: Session, access: ProfileAccess, visitor_id: UUID
) -> Visitor:
    actor = check_profile_access(db, access)
    if isinstance(access, VisitorDataAccess):
        if access.visitor_id != visitor_id:
            raise not_found()
        assert isinstance(actor, Visitor)
        return actor
    assert isinstance(actor, Staff)
    require_owned_visitor(db, actor, visitor_id)
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.id == visitor_id,
            Visitor.project_id == access.project_id,
            Visitor.deleted_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if visitor is None:
        raise not_found()
    return visitor


def require_visible_visitor_channel(
    db: Session,
    access: VisitorDataAccess,
    channel_id: str,
    channel_type: int,
) -> Visitor | Staff | UUID:
    visitor = reauthorize_visitor_data(db, access)
    if channel_type == 1 and channel_id.endswith("-staff"):
        try:
            identifier = UUID(channel_id[:-6])
        except ValueError:
            raise not_found() from None
        session = (
            db.query(VisitorSession.id)
            .filter(
                VisitorSession.project_id == access.project_id,
                VisitorSession.visitor_id == visitor.id,
                VisitorSession.staff_id == identifier,
                VisitorSession.status == "open",
            )
            .first()
        )
        member = (
            db.query(ChannelMember.id)
            .filter(
                ChannelMember.project_id == access.project_id,
                ChannelMember.channel_id
                == build_visitor_channel_id(visitor.id),
                ChannelMember.channel_type == 251,
                ChannelMember.member_id == identifier,
                ChannelMember.member_type == MEMBER_TYPE_STAFF,
                ChannelMember.deleted_at.is_(None),
            )
            .first()
        )
        staff = (
            db.query(Staff)
            .filter(
                Staff.id == identifier,
                Staff.project_id == access.project_id,
                Staff.deleted_at.is_(None),
                Staff.account_enabled.is_(True),
            )
            .populate_existing()
            .first()
        )
        if staff is None or (session is None and member is None):
            raise not_found()
        return staff
    if channel_type == 1 and channel_id.endswith("-agent"):
        try:
            identifier = UUID(channel_id[:-6])
        except ValueError:
            raise not_found() from None
        platform = (
            db.query(Platform)
            .filter(Platform.id == access.platform_id)
            .populate_existing()
            .first()
        )
        if platform is None or platform.agent_id != identifier:
            raise not_found()
        return identifier
    try:
        authorize_visitor_channel_id(visitor.id, channel_id, channel_type)
    except TGOAPIException:
        raise not_found() from None
    return visitor


def profile_guard(
    access: ProfileAccess,
    visitor_id: UUID | None = None,
    channel: tuple[str, int] | None = None,
) -> OperationGuard:
    def check() -> None:
        with visitor_chat_access.authority_database() as db:
            if channel is not None and isinstance(access, VisitorDataAccess):
                require_visible_visitor_channel(db, access, *channel)
            elif visitor_id is not None:
                require_profile_visitor(db, access, visitor_id)
            elif channel is not None and channel[1] == 251:
                try:
                    identifier = parse_visitor_channel_id(channel[0])
                except ValueError:
                    raise not_found() from None
                require_profile_visitor(db, access, identifier)
            elif (
                channel is not None
                and channel[1] == 1
                and channel[0].endswith("-staff")
            ):
                check_profile_access(db, access)
                try:
                    identifier = UUID(channel[0][:-6])
                except ValueError:
                    raise not_found() from None
                target = (
                    db.query(Staff.id)
                    .filter(
                        Staff.id == identifier,
                        Staff.project_id == access.project_id,
                        Staff.deleted_at.is_(None),
                    )
                    .first()
                )
                if target is None:
                    raise not_found()
            elif (
                channel is not None
                and channel[1] == 1
                and not channel[0].endswith(("-staff", "-agent"))
            ):
                try:
                    identifier = UUID(channel[0].removesuffix("-vtr"))
                except ValueError:
                    raise not_found() from None
                require_profile_visitor(db, access, identifier)
            else:
                check_profile_access(db, access)

    return OperationGuard(check=check)
