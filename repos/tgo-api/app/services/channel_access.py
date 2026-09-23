"""Authorize tenant-owned channels before any downstream message operation."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models import Staff, Visitor
from app.schemas.wukongim import WuKongIMConversation
from app.services.ai_client import ai_client
from app.utils.const import (
    CHANNEL_TYPE_CUSTOMER_SERVICE, CHANNEL_TYPE_PROJECT_STAFF,
)
from app.utils.encoding import (
    build_project_staff_channel_id, parse_visitor_channel_id,
)


def _identifier(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise TGOAPIException(
            "频道编号格式无效", "INVALID_CHANNEL", status_code=400,
        ) from exc


def _not_found() -> TGOAPIException:
    return TGOAPIException("频道不存在", "NOT_FOUND", status_code=404)


def _require_visitor(db: Session, user: Staff, identifier: UUID) -> None:
    from app.services.staff_conversation_scope import require_owned_visitor
    require_owned_visitor(db, user, identifier)
    visitor = db.query(Visitor.id).filter(
        Visitor.id == identifier,
        Visitor.project_id == user.project_id,
        Visitor.deleted_at.is_(None),
    ).first()
    if visitor is None:
        raise _not_found()


async def require_staff_channel_access(
    db: Session, user: Staff, channel_id: str, channel_type: int,
) -> None:
    """Check company ownership before staff-assignment restrictions."""
    if channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE:
        try:
            visitor_id = parse_visitor_channel_id(channel_id)
        except ValueError as exc:
            raise TGOAPIException(
                "频道编号格式无效", "INVALID_CHANNEL", status_code=400,
            ) from exc
        _require_visitor(db, user, visitor_id)
        return

    if channel_type == CHANNEL_TYPE_PROJECT_STAFF:
        if channel_id != build_project_staff_channel_id(user.project_id):
            raise _not_found()
        return

    if channel_type != 1:
        raise TGOAPIException(
            "不支持的频道类型", "INVALID_CHANNEL", status_code=400,
        )

    if channel_id.endswith("-staff"):
        staff_id = _identifier(channel_id[:-6])
        staff = db.query(Staff.id).filter(
            Staff.id == staff_id,
            Staff.project_id == user.project_id,
            Staff.deleted_at.is_(None),
        ).first()
        if staff is None:
            raise _not_found()
        return

    if channel_id.endswith("-agent"):
        agent_id = _identifier(channel_id[:-6])
        # AI owns agent records. Validate through its project-scoped HTTP API.
        await ai_client.get_agent(
            project_id=str(user.project_id), agent_id=str(agent_id),
            include_tools=False, include_collections=False,
        )
        return

    raw_id = channel_id[:-4] if channel_id.endswith("-vtr") else channel_id
    _require_visitor(db, user, _identifier(raw_id))


async def filter_staff_conversations(
    db: Session, user: Staff, records: list[WuKongIMConversation],
) -> list[WuKongIMConversation]:
    """Discard stale IM membership without trusting its tenant ownership."""
    decisions: dict[tuple[str, int], bool] = {}
    permitted: list[WuKongIMConversation] = []
    for record in records:
        key = (record.channel_id, record.channel_type)
        if key not in decisions:
            decisions[key] = await staff_can_access_channel(db, user, *key)
        if decisions[key]:
            permitted.append(record)
    return permitted


async def staff_can_access_channel(
    db: Session, user: Staff, channel_id: str, channel_type: int,
) -> bool:
    """Hide inaccessible channels; propagate infrastructure errors."""
    try:
        await require_staff_channel_access(db, user, channel_id, channel_type)
    except (TGOAPIException, HTTPException) as exc:
        if exc.status_code not in (400, 403, 404):
            raise
        return False
    return True
