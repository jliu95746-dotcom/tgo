"""Bounded, minimized excerpts from one committed closed staff session."""

import re
from datetime import datetime, timezone
from math import ceil, floor

from pydantic import JsonValue
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.channel_memory_clearance import ChannelMemoryClearance
from app.models.session_command_delivery import SessionCommandDelivery
from app.models.visitor_session import VisitorSession
from app.models.workflow_event_subscription import WorkflowEventSubscription
from app.schemas.wukongim import WuKongIMChannelMessageSyncResponse
from app.services.session_handoff_summary import _excerpt
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


class SnapshotUnavailable(Exception):
    """A fixed non-sensitive code for a source that cannot be summarized."""

    def __init__(self, code: str = "snapshot_boundary_unavailable") -> None:
        self.code = code
        super().__init__(code)


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _bounds(
    source: SessionCommandDelivery,
    session: VisitorSession,
) -> tuple[int, int, int]:
    if (
        source.kind != "close"
        or session.status != "closed"
        or source.session_id != session.id
        or source.project_id != session.project_id
        or source.visitor_id != session.visitor_id
        or source.channel_id != build_visitor_channel_id(session.visitor_id)
        or source.channel_type != CHANNEL_TYPE_CUSTOMER_SERVICE
        or session.closed_at is None
        or session.created_at is None
        or session.last_message_seq is None
        or session.last_message_seq <= 0
    ):
        raise SnapshotUnavailable()
    earliest = ceil(_utc(session.created_at).timestamp())
    latest = floor(_utc(session.closed_at).timestamp())
    if earliest > latest:
        raise SnapshotUnavailable()
    return earliest, latest, session.last_message_seq


def _redact(text: str) -> str:
    """Mask common identifiers without claiming general DLP coverage."""
    text = re.sub(r"https?://[^\s<>]+", "[链接]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+(?:\.[A-Z]{2,})?",
        "[邮箱]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9])\+?\d[\d ()-]{4,}\d(?:[xX])?",
        "[数字标识]",
        text,
    )
    text = re.sub(
        r"(?i)(密码|验证码|口令|password|token|secret)\s*[:：=]\s*\S+",
        r"\1：[已遮盖]",
        text,
    )
    return text[:280]


def closed_session_data(
    source: SessionCommandDelivery,
    session: VisitorSession,
    history: WuKongIMChannelMessageSyncResponse,
    earliest_seq: int = 0,
) -> dict[str, JsonValue]:
    earliest, latest, last_seq = _bounds(source, session)
    excerpts: list[JsonValue] = []
    seen: set[int] = set()
    # Never silently accept an upstream batch belonging to another channel.
    if any(
        row.channel_id != source.channel_id
        or row.channel_type != source.channel_type
        for row in history.messages
    ):
        raise SnapshotUnavailable("snapshot_channel_mismatch")
    for message in sorted(history.messages, key=lambda row: row.message_seq):
        if (
            message.message_seq > last_seq
            or message.message_seq < max(1, earliest_seq)
            or message.timestamp > latest
            or message.message_seq in seen
        ):
            continue
        if (
            message.from_uid.endswith("-vtr")
            and message.from_uid != f"{session.visitor_id}-vtr"
        ):
            raise SnapshotUnavailable("snapshot_sender_mismatch")
        item = _excerpt(message, earliest, earliest_seq)
        if item is None:
            continue
        seen.add(message.message_seq)
        excerpts.append(
            {
                "role": item.role,
                "content": _redact(item.content),
                "message_seq": item.message_seq,
                "timestamp": item.timestamp,
            }
        )
    if not excerpts:
        raise SnapshotUnavailable("snapshot_no_eligible_messages")
    # Keep both sides of the conversation in chronological order, with a
    # strict total bound. This is a fragment, never a complete transcript.
    selected = excerpts[-12:]
    assert session.closed_at is not None
    return {
        "snapshot_scope": "bounded_session_excerpts",
        "session_id": str(session.id),
        "closed_at": _utc(session.closed_at).isoformat(),
        "last_message_seq": last_seq,
        "sampled_message_count": len(selected),
        "has_earlier_messages": bool(history.more) or len(excerpts) > 12,
        "excerpts": selected,
    }


async def load_closed_session_snapshot(
    db: Session,
    subscription: WorkflowEventSubscription,
    source: SessionCommandDelivery,
) -> dict[str, JsonValue]:
    """Caller must recheck source authorization after this awaited read."""
    session = db.scalar(
        select(VisitorSession)
        .where(
            VisitorSession.id == source.session_id,
            VisitorSession.project_id == source.project_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        session is None
        or subscription.project_id != source.project_id
        or subscription.enabled_by_staff_id is None
    ):
        raise SnapshotUnavailable()
    _, _, last_seq = _bounds(source, session)
    clearances = db.scalars(
        select(ChannelMemoryClearance).where(
            ChannelMemoryClearance.project_id == source.project_id,
            ChannelMemoryClearance.channel_id == source.channel_id,
            ChannelMemoryClearance.channel_type == source.channel_type,
            or_(
                and_(
                    ChannelMemoryClearance.user_type == "staff",
                    ChannelMemoryClearance.user_id.in_(
                        [
                            subscription.enabled_by_staff_id,
                            source.actor_staff_id,
                        ]
                    ),
                ),
                and_(
                    ChannelMemoryClearance.user_type == "visitor",
                    ChannelMemoryClearance.user_id == source.visitor_id,
                ),
            ),
        )
    ).all()
    earliest_seq = max(
        (int(row.cleared_message_seq) + 1 for row in clearances),
        default=0,
    )
    if earliest_seq > last_seq:
        raise SnapshotUnavailable("snapshot_history_cleared")
    if not wukongim_client.enabled:
        raise SnapshotUnavailable("snapshot_history_unavailable")
    reader_id = subscription.enabled_by_staff_id
    # Freeze scalar and ORM inputs before releasing the transaction. No
    # locks or transaction snapshot span the IM network operation.
    frozen_source = SessionCommandDelivery(
        id=source.id,
        kind=source.kind,
        session_id=source.session_id,
        project_id=source.project_id,
        visitor_id=source.visitor_id,
        channel_id=source.channel_id,
        channel_type=source.channel_type,
    )
    frozen_session = VisitorSession(
        id=session.id,
        project_id=session.project_id,
        visitor_id=session.visitor_id,
        status=session.status,
        created_at=session.created_at,
        closed_at=session.closed_at,
        last_message_seq=last_seq,
    )
    db.commit()
    history = await wukongim_client.sync_channel_messages(
        login_uid=f"{reader_id}-staff",
        channel_id=frozen_source.channel_id,
        channel_type=frozen_source.channel_type,
        start_message_seq=last_seq,
        end_message_seq=max(0, earliest_seq - 1),
        pull_mode=0,
        limit=100,
        include_event_meta=1,
        event_summary_mode="full",
    )
    return closed_session_data(
        frozen_source,
        frozen_session,
        history,
        earliest_seq,
    )
