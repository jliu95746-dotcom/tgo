"""Forward original messages independently of admission, in customer order."""

import hashlib
from sqlalchemy import select, text
from app.core.database import SessionLocal
from app.models.customer_turn import CustomerTurn, CustomerTurnPart


def pending_actors() -> list[str]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(CustomerTurn.actor_key)
                .join(CustomerTurnPart, CustomerTurnPart.turn_id == CustomerTurn.id)
                .where(CustomerTurnPart.forwarded.is_(False))
                .distinct()
                .limit(32)
            )
        )


async def forward(actor: str) -> None:
    from app.services.chat_service import send_user_message_to_wukongim

    for _ in range(32):
        with SessionLocal() as db:
            if db.get_bind().dialect.name == "postgresql":
                key = int.from_bytes(
                    hashlib.sha256(("turn-original:" + actor).encode()).digest()[:8],
                    "big",
                    signed=True,
                )
                if not db.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}
                ):
                    return
            result = db.execute(
                select(CustomerTurnPart, CustomerTurn)
                .join(CustomerTurn, CustomerTurn.id == CustomerTurnPart.turn_id)
                .where(
                    CustomerTurn.actor_key == actor,
                    CustomerTurnPart.forwarded.is_(False),
                )
                .order_by(CustomerTurnPart.created_at, CustomerTurnPart.position)
                .with_for_update(of=CustomerTurnPart, skip_locked=True)
                .limit(1)
            ).first()
            if result is None:
                return
            part, turn = result
            payload = part.payload
            if part.media_pending and not turn.failure:
                return
            if payload.get("forward_user_message_to_wukongim", True):
                if payload.get("visitor_name") or payload.get("visitor_avatar"):
                    from app.services.wukongim_client import wukongim_client

                    await wukongim_client.send_visitor_profile_updated(
                        visitor_id=str(turn.visitor_id),
                        channel_id=turn.channel_id,
                        channel_type=turn.channel_type,
                    )
                extra = payload.get("extra")
                extra = dict(extra) if isinstance(extra, dict) else {}
                extra["message_id"] = part.source_message_id
                content, kind = str(payload.get("message", "")), payload.get(
                    "msg_type", 1
                )
                if part.media_pending or payload.get("media_error"):
                    content, kind = "[图片或语音未接收完整，请补充文字或重新发送]", 1
                    extra["original_media_type"] = payload.get("msg_type")
                elif kind in (2, 3):
                    from app.services.storage import get_storage

                    content = get_storage().resolve_url(content)
                await send_user_message_to_wukongim(
                    from_uid=f"{turn.visitor_id}-vtr",
                    channel_id=turn.channel_id,
                    channel_type=turn.channel_type,
                    content=content,
                    msg_type=kind,
                    extra=extra,
                    require_delivery=True,
                )
            part.forwarded = True
            db.commit()
