"""Canonical customer context for staff plugin requests and their responses."""

from dataclasses import dataclass
from uuid import UUID

from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models import Staff, VisitorSession
from app.schemas.plugin import VisitorInfo
from app.schemas.staff_data_access import StaffDataAccess
from app.services import visitor_chat_access
from app.services.channel_access import require_staff_channel_access
from app.services.profile_access import (
    OperationGuard,
    not_found,
    profile_guard,
    require_profile_access,
    require_profile_visitor,
)
from app.services.visitor_access import denied
from app.utils.encoding import build_visitor_channel_id, get_session_id


@dataclass(frozen=True)
class PluginRequestScope:
    access: StaffDataAccess
    visitor_id: str | None
    visitor: VisitorInfo | None
    guard: OperationGuard


async def capture_plugin_scope(
    db: Session,
    staff: Staff,
    credentials: HTTPAuthorizationCredentials | None,
    visitor_id: str | None,
    claimed: VisitorInfo | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> PluginRequestScope:
    access = require_profile_access(db, credentials, None)
    if (
        not isinstance(access, StaffDataAccess)
        or access.staff_id != staff.id
        or access.project_id != staff.project_id
    ):
        raise denied()
    raw = visitor_id or (claimed.id if claimed is not None else None)
    try:
        identifier = UUID(raw) if raw is not None else None
        agent = UUID(agent_id) if agent_id is not None else None
        if claimed is not None and UUID(claimed.id) != identifier:
            raise ValueError("Conflicting visitor context")
    except ValueError:
        raise TGOAPIException(
            "插件会话编号无效", "INVALID_CONTEXT", status_code=400
        ) from None
    visitor = (
        require_profile_visitor(db, access, identifier)
        if identifier is not None
        else None
    )
    authoritative = (
        None
        if visitor is None
        else VisitorInfo(
            id=str(visitor.id),
            platform_open_id=visitor.platform_open_id,
            name=visitor.name or visitor.nickname or visitor.nickname_zh,
            email=visitor.email,
            phone=visitor.phone_number,
            avatar=visitor.avatar_url,
            metadata=visitor.custom_attributes or {},
        )
    )
    base = profile_guard(access, visitor_id=identifier)

    def check_session(current: Session) -> None:
        if session_id is None:
            return
        if identifier is not None:
            if session_id == get_session_id(
                f"{access.staff_id}-staff", build_visitor_channel_id(identifier), 251
            ):
                return
            try:
                parsed = UUID(session_id)
            except ValueError:
                raise not_found() from None
            record = (
                current.query(VisitorSession.id)
                .filter(
                    VisitorSession.id == parsed,
                    VisitorSession.project_id == access.project_id,
                    VisitorSession.visitor_id == identifier,
                )
                .first()
            )
            if record is not None:
                return
        elif agent is not None and session_id == get_session_id(
            f"{access.staff_id}-staff", f"{agent}-agent", 1
        ):
            return
        raise not_found()

    def check() -> None:
        base.check()
        with visitor_chat_access.authority_database() as current:
            check_session(current)

    guard = OperationGuard(check=check)
    guard.check()
    if agent is not None:
        await require_staff_channel_access(db, staff, f"{agent}-agent", 1)
        guard.check()
    return PluginRequestScope(
        access=access,
        visitor_id=str(identifier) if identifier is not None else None,
        visitor=authoritative,
        guard=guard,
    )
