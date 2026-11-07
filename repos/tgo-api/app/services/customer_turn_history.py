"A confirmed external send remains visible even if staff IM was \
unavailable."

from datetime import datetime, timedelta, timezone
from uuid import UUID
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.customer_turn import CustomerTurn
from app.services.customer_turn_context import TurnExecution, current_execution
from app.services.customer_turn_store import TurnStore


def pending() -> list[UUID]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(CustomerTurn.id)
                .where(
                    CustomerTurn.state == "sent",
                    CustomerTurn.im_published_at.is_(None),
                    CustomerTurn.due_at <= datetime.now(timezone.utc),
                )
                .order_by(CustomerTurn.delivered_at)
                .limit(32)
            )
        )


async def publish(turn_id: UUID) -> None:
    with SessionLocal() as db:
        row = TurnStore(db).get(turn_id)
        if row.state != "sent" or row.im_published_at is not None:
            return
        context = TurnExecution(
            row.id,
            row.version,
            row.project_id,
            row.platform_id,
            row.visitor_id,
            True,
            staged=True,
        )
        channel_id, channel_type = row.channel_id, row.channel_type
        client_msg_no, from_uid = row.client_msg_no, row.from_uid or ""
        reply = row.reply_text
    token = current_execution.set(context)
    try:
        from app.services.chat_service import forward_ai_event_to_wukongim

        result = await forward_ai_event_to_wukongim(
            event_type="workflow_completed",
            event_data={
                "data": {
                    "success": True,
                    "final_content": reply,
                    "total_chunks": 0,
                }
            },
            channel_id=channel_id,
            channel_type=channel_type,
            client_msg_no=client_msg_no,
            from_uid=from_uid,
        )
        if result != reply:
            raise RuntimeError(
                "Staff history did not acknowledge the final reply"
            )
        with SessionLocal() as db:
            TurnStore(db).get(
                turn_id, lock=True
            ).im_published_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:
        with SessionLocal() as db:
            TurnStore(db).get(turn_id, lock=True).due_at = datetime.now(
                timezone.utc
            ) + timedelta(seconds=5)
            db.commit()
    finally:
        current_execution.reset(token)
