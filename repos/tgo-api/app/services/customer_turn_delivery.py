"""Dispatch ready channel replies after generation or a service restart."""

from datetime import datetime, timedelta, timezone
import logging
from uuid import UUID
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models import Platform
from app.models.customer_turn import CustomerTurn
from app.services.customer_turn_store import TurnStore
from app.services.platform_message_client import forward_customer_turn

logger = logging.getLogger(__name__)


def due_turns() -> list[UUID]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(CustomerTurn.id)
                .join(Platform, CustomerTurn.platform_id == Platform.id)
                .where(
                    CustomerTurn.state == "ready",
                    CustomerTurn.external_delivery.is_(True),
                    CustomerTurn.due_at <= datetime.now(timezone.utc),
                    Platform.type.notin_(["website", "wechat_local"]),
                    Platform.is_active.is_(True),
                )
                .order_by(CustomerTurn.created_at)
                .limit(32)
            )
        )


async def deliver(turn_id: UUID) -> None:
    with SessionLocal() as db:
        store = TurnStore(db)
        row = store.get(turn_id)
        if row.state != "ready":
            return
        version = row.version
        project_id, platform_id, channel_id = (
            row.project_id,
            row.platform_id,
            row.channel_id,
        )
        source = store.parts(row.id)[-1].source_message_id
    try:
        await forward_customer_turn(
            project_id=project_id,
            platform_id=platform_id,
            channel_id=channel_id,
            source_message_id=source,
            turn_id=turn_id,
            version=version,
        )
    except Exception as error:
        logger.warning(
            "Customer channel delivery waiting turn=%s error_type=%s",
            turn_id,
            type(error).__name__,
        )
        with SessionLocal() as db:
            row = TurnStore(db).get(turn_id, lock=True)
            if row.version == version and row.state == "ready":
                row.due_at = datetime.now(timezone.utc) + timedelta(seconds=5)
                row.failure = "渠道连接等待恢复；尚未发送"
                db.commit()
