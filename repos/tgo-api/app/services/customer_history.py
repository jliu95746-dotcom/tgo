"""Resolve recognized history only inside the current customer and group actor."""

from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import MediaAnalysisResult, Visitor
from app.models.customer_turn import CustomerTurn
from app.services.customer_turn_context import current_execution
from app.services.customer_turn_store import TurnStore
from app.utils.encoding import parse_visitor_channel_id


@dataclass
class HistoryScope:
    media: dict[str, str] = field(default_factory=dict)
    author: str | None = None
    replies: set[str] = field(default_factory=set)
    selectors: dict[str, str] = field(default_factory=dict)


def history_scope(channel_id: str) -> HistoryScope:
    result = HistoryScope()
    try:
        visitor_id = parse_visitor_channel_id(channel_id)
    except ValueError:
        return result
    with SessionLocal() as db:
        visitor = db.get(Visitor, visitor_id)
        if visitor is None:
            return result
        rows = db.scalars(
            select(MediaAnalysisResult)
            .where(
                MediaAnalysisResult.visitor_id == visitor.id,
                MediaAnalysisResult.project_id == visitor.project_id,
                MediaAnalysisResult.platform_id == visitor.platform_id,
                MediaAnalysisResult.can_continue.is_(True),
            )
            .order_by(MediaAnalysisResult.created_at.desc())
            .limit(60)
        )
        result.media = {
            row.source_message_id: row.normalized_text
            for row in rows
            if row.normalized_text
        }
        context = current_execution.get()
        if context is not None:
            store = TurnStore(db)
            turn = store.get(context.turn_id)
            parts = store.parts(turn.id)
            extra = parts[0].payload.get("extra") if parts else None
            if isinstance(extra, dict) and (
                extra.get("wechat_local_group") is True
                or extra.get("reception_conversation")
            ):
                field = (
                    "wechat_local_author"
                    if extra.get("wechat_local_group") is True
                    else "reception_author"
                )
                author = extra.get(field)
                # Missing actor metadata never grants access to other members'
                # conversation history during an automatic group reply.
                result.author = str(author) if author else "unresolved"
                result.selectors[field] = result.author
                if extra.get("reception_conversation"):
                    result.selectors["reception_conversation"] = str(
                        extra["reception_conversation"]
                    )
                result.replies = set(
                    db.scalars(
                        select(CustomerTurn.client_msg_no).where(
                            CustomerTurn.actor_key == turn.actor_key,
                            CustomerTurn.state == "sent",
                        )
                    )
                )
    return result
