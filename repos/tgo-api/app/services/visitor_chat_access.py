"""Original visitor authority through chat ingress, background work and outputs."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import hmac
from typing import TypeAlias, TypeVar
from uuid import UUID

from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr
from sqlalchemy.orm import Session
from starlette.responses import Response
from starlette.types import Message, Receive, Scope, Send

from app.core.database import SessionLocal
from app.core.security import require_internal_service_token
from app.models import Platform, Project, Visitor, VisitorServiceStatus
from app.models import Staff
from app.models.platform import DIRECT_VISITOR_PLATFORM_TYPES
from app.schemas.staff_data_access import StaffDataAccess
from app.schemas.channel_chat_access import ChannelChatAccess
from app.schemas.visitor_data_access import VisitorDataAccess
from app.services.visitor_access import denied
from app.services.visitor_data_access import (
    require_visitor_data_access,
    reauthorize_visitor_data,
    authorize_visitor_channel_id,
)

ChatAccess: TypeAlias = VisitorDataAccess | ChannelChatAccess | StaffDataAccess
_current: ContextVar[ChatAccess | None] = ContextVar(
    "original_chat_access", default=None
)
Result = TypeVar("Result")


def check_chat_access(db: Session, access: ChatAccess) -> Visitor | Staff | None:
    if isinstance(access, StaffDataAccess):
        from app.services.profile_access import check_profile_access

        return check_profile_access(db, access)
    if isinstance(access, VisitorDataAccess):
        return reauthorize_visitor_data(db, access)
    require_internal_service_token(access.service_token.get_secret_value())
    platform: Platform | None = (
        db.query(Platform)
        .join(Project, Platform.project_id == Project.id)
        .filter(
            Platform.id == access.platform_id,
            Platform.project_id == access.project_id,
            ~Platform.type.in_(DIRECT_VISITOR_PLATFORM_TYPES),
            Platform.deleted_at.is_(None),
            Platform.is_active.is_(True),
            Project.deleted_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if platform is None or not hmac.compare_digest(
        platform.api_key, access.platform_api_key.get_secret_value()
    ):
        raise denied()
    if access.visitor_id is None:
        return None
    visitor: Visitor | None = (
        db.query(Visitor)
        .filter(
            Visitor.id == access.visitor_id,
            Visitor.platform_id == access.platform_id,
            Visitor.project_id == access.project_id,
            Visitor.deleted_at.is_(None),
            Visitor.access_retired_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if visitor is None:
        raise denied()
    return visitor


async def get_or_create_channel_visitor(
    db: Session,
    platform: Platform,
    platform_open_id: str,
    nickname: str | None,
    avatar_url: str | None,
) -> tuple[Visitor, bool]:
    from app.services.chat_service import get_or_create_visitor

    visitor: Visitor
    created: bool
    visitor, created = await get_or_create_visitor(
        db, platform, platform_open_id, nickname, avatar_url
    )
    return visitor, created


async def resolve_chat_visitor(
    db: Session,
    platform: Platform,
    from_uid: str | None,
    credentials: HTTPAuthorizationCredentials | None,
    service_token: str | None,
    *,
    nickname: str | None = None,
    avatar_url: str | None = None,
    channel_id: str | None = None,
    channel_type: int = 251,
) -> tuple[Visitor, bool, ChatAccess]:
    if channel_type != 251:
        raise denied()
    if platform.type not in DIRECT_VISITOR_PLATFORM_TYPES:
        require_internal_service_token(service_token)
        assert service_token is not None
        access = ChannelChatAccess(
            project_id=platform.project_id,
            platform_id=platform.id,
            platform_api_key=SecretStr(platform.api_key),
            service_token=SecretStr(service_token),
        )
        check_chat_access(db, access)
        if not from_uid:
            raise denied()
        if channel_id is not None:
            from app.utils.encoding import parse_visitor_channel_id

            try:
                channel_visitor_id = parse_visitor_channel_id(channel_id)
            except ValueError:
                raise denied() from None
            owned = (
                db.query(Visitor.id)
                .filter(
                    Visitor.id == channel_visitor_id,
                    Visitor.project_id == access.project_id,
                    Visitor.platform_id == access.platform_id,
                    Visitor.platform_open_id == from_uid,
                    Visitor.deleted_at.is_(None),
                    Visitor.access_retired_at.is_(None),
                )
                .first()
            )
            if owned is None:
                raise denied()
        visitor, changed = await get_or_create_channel_visitor(
            db, platform, from_uid, nickname, avatar_url
        )
        access = access.model_copy(update={"visitor_id": visitor.id})
        check_chat_access(db, access)
        return visitor, changed, access
    proof = require_visitor_data_access(db, platform, credentials)
    visitor = reauthorize_visitor_data(db, proof)
    if from_uid is not None and from_uid not in (
        visitor.platform_open_id,
        str(visitor.id),
        f"{visitor.id}-vtr",
    ):
        raise denied()
    check_chat_channel(
        proof, visitor.id, channel_id or f"{visitor.id}-vtr", channel_type
    )
    from app.services.company_entitlements import require_human_service

    require_human_service(db, platform.project_id, visitor.id)
    changed = False
    if nickname:
        for field in ("nickname", "name", "nickname_zh"):
            if getattr(visitor, field) != nickname:
                setattr(visitor, field, nickname)
                changed = True
    if avatar_url and visitor.avatar_url != avatar_url:
        visitor.avatar_url = avatar_url
        changed = True
    if visitor.service_status == VisitorServiceStatus.CLOSED.value:
        visitor.service_status = VisitorServiceStatus.NEW.value
        changed = True
    if changed:
        db.commit()
        check_chat_access(db, proof)
    return visitor, changed, proof


def check_chat_channel(
    access: ChatAccess, visitor_id: UUID, channel_id: str, channel_type: int
) -> None:
    if isinstance(access, StaffDataAccess):
        raise denied()
    if access.visitor_id != visitor_id:
        raise denied()
    authorize_visitor_channel_id(visitor_id, channel_id, channel_type)


@contextmanager
def authority_database() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db


def check_current_chat_access(access: ChatAccess | None = None) -> None:
    original = access if access is not None else _current.get()
    if original is not None:
        with authority_database() as db:
            check_chat_access(db, original)


@contextmanager
def scoped_chat_access(access: ChatAccess | None) -> Iterator[None]:
    token = _current.set(access)
    try:
        check_current_chat_access()
        yield
    finally:
        _current.reset(token)


async def guarded_chat_call(
    factory: Callable[[], Awaitable[Result]], access: ChatAccess | None = None
) -> Result:
    check_current_chat_access(access)
    result = await factory()
    check_current_chat_access(access)
    return result


class AuthorizedChatResponse(Response):
    def __init__(self, response: Response, access: ChatAccess):
        super().__init__(
            status_code=response.status_code, headers=dict(response.headers)
        )
        self.response = response
        self.access = access

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def checked_send(message: Message) -> None:
            if message["type"] in ("http.response.start", "http.response.body"):
                check_current_chat_access(self.access)
            await send(message)

        await self.response(scope, receive, checked_send)
