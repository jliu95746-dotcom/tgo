"""Public waiting summaries omit message history, contacts and private metadata."""

from sqlalchemy.orm import Session

from app.models import Staff, Visitor, VisitorWaitingQueue, WaitingStatus
from app.schemas.wukongim import ChannelInfo, WuKongIMConversation
from app.schemas.visitor import resolve_visitor_display_name
from app.services.company_email import utc
from app.utils.encoding import build_visitor_channel_id


def queue_previews(
    db: Session, actor: Staff, limit: int, offset: int
) -> tuple[list[WuKongIMConversation], list[ChannelInfo], int]:
    query = (
        db.query(VisitorWaitingQueue)
        .join(Visitor, VisitorWaitingQueue.visitor_id == Visitor.id)
        .filter(
            VisitorWaitingQueue.project_id == actor.project_id,
            VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
            Visitor.project_id == actor.project_id,
            Visitor.deleted_at.is_(None),
        )
    )
    total = query.count()
    entries = (
        query.order_by(
            VisitorWaitingQueue.priority.desc(), VisitorWaitingQueue.entered_at
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    conversations: list[WuKongIMConversation] = []
    channels: list[ChannelInfo] = []
    for entry in entries:
        visitor = entry.visitor
        channel = build_visitor_channel_id(visitor.id)
        conversations.append(
            WuKongIMConversation(
                channel_id=channel,
                channel_type=251,
                unread=0,
                timestamp=int(utc(entry.entered_at).timestamp()),
                last_msg_seq=0,
                last_client_msg_no="",
                version=0,
                recents=[],
            )
        )
        channels.append(
            ChannelInfo(
                channel_id=channel,
                channel_type=251,
                entity_type="visitor",
                name=resolve_visitor_display_name(
                    visitor.name, visitor.nickname, visitor.nickname_zh, "zh"
                ),
                avatar=visitor.avatar_url or "",
                extra={"queue_summary": True, "service_status": "queued"},
            )
        )
    return conversations, channels, total
