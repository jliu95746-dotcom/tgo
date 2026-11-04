"""Bounded current-session excerpts for staff handoff."""

import re
from math import ceil
from datetime import timezone
from typing import Literal, cast
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Staff, Visitor, VisitorSession
from app.models.channel_memory_clearance import (
    ChannelMemoryClearance, ClearanceUserType,
)
from app.schemas.staff_data_access import StaffDataAccess
from app.schemas.visitor_session import (
    SessionHandoffExcerpt, SessionHandoffSummary,
)
from app.schemas.wukongim import (
    WuKongIMMessage, WuKongIMChannelMessageSyncResponse,
)
from app.core.exceptions import TGOAPIException
from app.services.profile_access import OperationGuard, not_found
from app.services.staff_conversation_scope import require_owned_visitor
from app.services.staff_record_access import check_staff_record_permission
from app.services.visitor_chat_access import authority_database
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


MEDIA_EXCERPTS: dict[int, str] = {
    2: "[图片] 请查看原消息核对内容",
    3: "[附件] 请查看原消息核对内容",
    4: "[语音] 请查看原消息核对内容",
}


def _rich_text_excerpt(payload: dict[str, object]) -> str | None:
    raw_text = payload.get("content")
    text = (
        re.sub(r"\s+", " ", raw_text).strip()
        if isinstance(raw_text, str) else ""
    )
    attachments = []
    if isinstance(payload.get("images"), list) and payload["images"]:
        attachments.append("图片")
    if isinstance(payload.get("file"), dict) and payload["file"]:
        attachments.append("附件")
    if not attachments:
        return text[:280] or None
    marker = f"[含{'、'.join(attachments)}，请查看原消息核对]"
    return f"{text[:279 - len(marker)]} {marker}" if text else marker


def _current_session(
    db: Session, access: StaffDataAccess, visitor_id: UUID,
) -> VisitorSession:
    staff = check_staff_record_permission(db, access, "visitors:read")
    assert isinstance(staff, Staff)
    require_owned_visitor(db, staff, visitor_id)
    visitor = db.query(Visitor.id).filter(
        Visitor.id == visitor_id,
        Visitor.project_id == access.project_id,
        Visitor.deleted_at.is_(None),
    ).first()
    if visitor is None:
        raise not_found()
    session = db.query(VisitorSession).filter(
        VisitorSession.visitor_id == visitor_id,
        VisitorSession.project_id == access.project_id,
        VisitorSession.status == "open",
    ).order_by(
        VisitorSession.created_at.desc(), VisitorSession.id.desc(),
    ).first()
    if session is None:
        raise not_found()
    return session


def _excerpt(
    message: WuKongIMMessage, earliest_timestamp: int, earliest_seq: int,
) -> SessionHandoffExcerpt | None:
    if (
        message.timestamp < earliest_timestamp
        or message.message_seq < earliest_seq
        or message.error
    ):
        return None
    payload = message.payload
    content = payload.get("content")
    payload_type = payload.get("type")
    role: Literal["customer", "staff", "assistant"]
    if payload_type == 100:
        meta = message.event_meta or {}
        if not (message.end == 1 or meta.get("completed") is True):
            return None
        content = None
        for event in meta.get("events", []):
            if isinstance(event, dict) and event.get("event_key") == "main":
                snapshot = event.get("snapshot")
                if (
                    isinstance(snapshot, dict)
                    and snapshot.get("kind") == "text"
                ):
                    content = snapshot.get("text")
        role = "assistant"
    elif isinstance(payload_type, int) and payload_type in MEDIA_EXCERPTS:
        # A media URL or filename is not a transcript or a customer request.
        content = MEDIA_EXCERPTS[payload_type]
    elif payload_type == 12:
        content = _rich_text_excerpt(payload)
    elif payload_type not in (None, 1):
        return None
    if payload_type != 100:
        if message.from_uid.endswith("-vtr"):
            role = "customer"
        elif message.from_uid.endswith("-staff"):
            role = "staff"
        elif message.from_uid.endswith("-agent"):
            role = "assistant"
        else:
            return None
    if not isinstance(content, str):
        return None
    content = re.sub(r"\s+", " ", content).strip()[:280]
    if not content:
        return None
    return SessionHandoffExcerpt(
        role=role, content=content,
        message_seq=message.message_seq, timestamp=message.timestamp,
    )


def summarize_messages(
    session: VisitorSession, history: WuKongIMChannelMessageSyncResponse,
    earliest_seq: int = 0,
) -> SessionHandoffSummary:
    """Use recent text and media indicators from the current session."""
    started = session.created_at
    started_utc = (
        started.replace(tzinfo=timezone.utc) if started.tzinfo is None
        else started.astimezone(timezone.utc)
    )
    # IM timestamps have second precision. Exclude an ambiguous boundary
    # second rather than risk showing the previous session's last message.
    earliest = ceil(started_utc.timestamp())
    excerpts = [
        item for message in sorted(
            history.messages, key=lambda row: row.message_seq,
        )
        if (item := _excerpt(message, earliest, earliest_seq)) is not None
    ]
    return SessionHandoffSummary(
        session_id=session.id,
        visitor_id=session.visitor_id,
        sampled_message_count=len(excerpts),
        has_earlier_messages=bool(history.more),
        latest_customer_messages=[
            item for item in excerpts if item.role == "customer"
        ][-3:],
        latest_replies=[
            item for item in excerpts if item.role != "customer"
        ][-2:],
    )


async def load_handoff_summary(
    db: Session, access: StaffDataAccess, visitor_id: UUID,
) -> tuple[SessionHandoffSummary, OperationGuard]:
    session = _current_session(db, access, visitor_id)
    session_id = session.id
    channel_id = build_visitor_channel_id(visitor_id)
    clearance = db.query(ChannelMemoryClearance).filter(
        ChannelMemoryClearance.project_id == access.project_id,
        ChannelMemoryClearance.user_id == access.staff_id,
        ChannelMemoryClearance.user_type == ClearanceUserType.STAFF.value,
        ChannelMemoryClearance.channel_id == channel_id,
        ChannelMemoryClearance.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE,
    ).first()
    start_seq = (
        cast(int, clearance.cleared_message_seq) + 1 if clearance else 0
    )

    def check() -> None:
        with authority_database() as fresh_db:
            if _current_session(fresh_db, access, visitor_id).id != session_id:
                raise not_found()

    guard = OperationGuard(check)
    if not wukongim_client.enabled:
        raise TGOAPIException(
            "会话消息服务不可用", "MESSAGE_HISTORY_UNAVAILABLE", status_code=503,
        )
    # The IM 0/0 initial page returns the latest messages. Applying the
    # clearance as a request cursor could instead return an older page.
    history = await wukongim_client.sync_channel_messages(
        login_uid=f"{access.staff_id}-staff",
        channel_id=channel_id,
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        start_message_seq=0,
        limit=100,
        include_event_meta=1,
        event_summary_mode="full",
    )
    guard.check()
    return summarize_messages(session, history, start_seq), guard
