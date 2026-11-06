"""A timeout grants one source message, never a permanent automatic mode."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import Platform, Project, Staff, Visitor, VisitorSession

MAX_AI_FALLBACK_RETRIES = 3


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass(frozen=True)
class FallbackSource:
    project_id: UUID
    platform_id: UUID
    visitor_id: UUID
    session_id: UUID
    staff_id: UUID
    source_message_id: str
    source_at: datetime
    agent_id: UUID | None
    response_client_msg_no: str | None = None


_current: ContextVar[FallbackSource | None] = ContextVar(
    "timed_ai_fallback_source", default=None
)


def policy_allows(platform: Platform, visitor: Visitor) -> bool:
    timeout = platform.fallback_to_ai_timeout
    return (
        platform.type != "wechat_local"
        and platform.ai_mode == "assist"
        and timeout is not None and timeout > 0
        and platform.is_active and platform.deleted_at is None
        and visitor.project_id == platform.project_id
        and visitor.platform_id == platform.id
        and visitor.deleted_at is None and visitor.access_retired_at is None
        and visitor.service_status == "active"
        and visitor.service_mode is None and visitor.ai_disabled is not True
    )


def candidate(platform: Platform, visitor: Visitor) -> bool:
    timeout = platform.fallback_to_ai_timeout
    return (
        policy_allows(platform, visitor) and timeout is not None
        and visitor.is_last_message_from_visitor is True
        and bool(visitor.last_client_msg_no)
        and visitor.last_message_at is not None
        and utc(visitor.last_message_at)
        <= datetime.now(timezone.utc) - timedelta(seconds=timeout)
        and visitor.ai_fallback_retry_count < MAX_AI_FALLBACK_RETRIES
    )


def active_session(db: Session, visitor: Visitor) -> VisitorSession | None:
    return (
        db.query(VisitorSession)
        .filter(
            VisitorSession.project_id == visitor.project_id,
            VisitorSession.visitor_id == visitor.id,
            VisitorSession.status == "open",
        )
        .order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc())
        .populate_existing().first()
    )


def capture_source(
    db: Session, platform: Platform, visitor: Visitor
) -> FallbackSource | None:
    if not candidate(platform, visitor):
        return None
    session = active_session(db, visitor)
    if (
        session is None or session.staff_id is None
        or visitor.last_client_msg_no is None
        or visitor.last_message_at is None
    ):
        return None
    return FallbackSource(
        project_id=platform.project_id, platform_id=platform.id,
        visitor_id=visitor.id, session_id=session.id,
        staff_id=session.staff_id,
        source_message_id=visitor.last_client_msg_no,
        source_at=utc(visitor.last_message_at),
        agent_id=platform.agent_id,
    )


def still_current(db: Session, source: FallbackSource) -> bool:
    db.expire_all()
    platform = db.get(Platform, source.platform_id)
    visitor = db.get(Visitor, source.visitor_id)
    project = db.get(Project, source.project_id)
    staff = db.get(Staff, source.staff_id)
    if (
        platform is None or visitor is None or project is None
        or project.deleted_at is not None
        or not policy_allows(platform, visitor)
        or platform.project_id != source.project_id
        or platform.agent_id != source.agent_id
        or staff is None or staff.project_id != source.project_id
        or not staff.account_enabled or not staff.is_active
        or staff.deleted_at is not None
    ):
        return False
    session = active_session(db, visitor)
    if (
        session is None or session.id != source.session_id
        or session.staff_id != source.staff_id
        or session.platform_id not in (None, source.platform_id)
    ):
        return False
    if (
        source.response_client_msg_no is not None
        and visitor.last_client_msg_no == source.response_client_msg_no
        and visitor.is_last_message_from_ai is True
        and visitor.is_last_message_from_visitor is False
    ):
        return True  # An observed receipt is not a new customer message.
    return (
        candidate(platform, visitor)
        and visitor.last_client_msg_no == source.source_message_id
        and visitor.last_message_at is not None
        and utc(visitor.last_message_at) == source.source_at
    )


def require_source(db: Session, source: FallbackSource) -> None:
    if not still_current(db, source):
        from app.services.ai_reply_control import ReplyStopped

        raise ReplyStopped("本次超时接管条件已变化，AI 回复已停止。")


def check_current_fallback() -> None:
    source = _current.get()
    if source is not None:
        with SessionLocal() as db:
            require_source(db, source)


def allows_timed_reply(db: Session, project_id: str, visitor_id: str) -> bool:
    source = _current.get()
    if source is None:
        return False
    if (
        str(source.project_id) != project_id
        or str(source.visitor_id) != visitor_id
    ):
        from app.services.ai_reply_control import ReplyStopped

        raise ReplyStopped("超时接管不属于当前客户会话。")
    require_source(db, source)
    return True


@contextmanager
def fallback_scope(source: FallbackSource) -> Iterator[None]:
    token = _current.set(source)
    try:
        check_current_fallback()
        yield
    finally:
        _current.reset(token)
