"""Release abandoned generation reservations and reconcile ambiguous sends."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.ai_usage import AIUsageReservation
from app.services import ai_usage
from app.services.company_email import utc
from app.services.platform_message_client import get_ai_delivery_receipt
from app.services.wukongim_client import wukongim_client

logger = get_logger(__name__)


def release_abandoned() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(AIUsageReservation.id)
                .where(
                    AIUsageReservation.status == "reserved",
                    AIUsageReservation.expires_at <= now,
                )
                .limit(100)
            )
        )
    for identifier in ids:
        with SessionLocal() as db:
            row = db.get(AIUsageReservation, identifier)
            assert row is not None
            row = ai_usage.owned(db, row.project_id, row.id, row.lease_id)
            if row.status == "reserved" and utc(row.expires_at) <= now:
                ai_usage.release(db, row.project_id, row.id, row.lease_id)
                db.commit()


async def reconcile_deliveries() -> None:
    await asyncio.to_thread(release_abandoned)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(AIUsageReservation)
                .where(
                    AIUsageReservation.status.in_(["publishing", "review"]),
                    AIUsageReservation.expires_at <= now,
                )
                .order_by(AIUsageReservation.expires_at)
                .limit(100)
            )
        )
        candidates = [
            (row.id, row.project_id, row.lease_id, row.receipt or {}) for row in rows
        ]
    for identifier, project_id, lease, receipt in candidates:
        try:
            accepted = False
            rejected = False
            if receipt.get("kind") == "wecom":
                platform_id, source = receipt.get("platform_id"), receipt.get(
                    "source_message_id"
                )
                if not isinstance(platform_id, str) or not isinstance(source, str):
                    raise ValueError("Missing platform receipt identity")
                result = await get_ai_delivery_receipt(
                    project_id, UUID(platform_id), source
                )
                accepted = result.state == "accepted"
            elif receipt.get("kind") == "im":
                channel, kind, client_no = (
                    receipt.get("channel_id"),
                    receipt.get("channel_type"),
                    receipt.get("client_msg_no"),
                )
                if (
                    not isinstance(channel, str)
                    or type(kind) is not int
                    or not isinstance(client_no, str)
                ):
                    raise ValueError("Missing IM receipt identity")
                message = await wukongim_client.get_message_by_client_msg_no(
                    channel_id=channel,
                    channel_type=kind,
                    client_msg_no=client_no,
                    raise_on_error=True,
                )
                accepted = bool(
                    message
                    and message.end == 1
                    and not message.error
                    and message.payload.get("content")
                )
                rejected = bool(message and message.end == 1 and message.error)
            with SessionLocal() as db:
                row = ai_usage.owned(db, project_id, identifier, lease)
                if row.status not in {"publishing", "review"}:
                    continue
                if accepted:
                    ai_usage.settle(db, project_id, identifier, lease)
                elif rejected:
                    ai_usage.release(
                        db, project_id, identifier, lease, delivery_rejected=True
                    )
                else:
                    row.status = "review"
                    row.expires_at = now + timedelta(minutes=5)
                db.commit()
        except Exception as exc:
            logger.warning("AI quota receipt recovery failed: %s", type(exc).__name__)
            # Give newer receipts a chance on the next bounded sweep. Never assume
            # that a transport failure means the original reply was not delivered.
            try:
                with SessionLocal() as db:
                    row = ai_usage.owned(db, project_id, identifier, lease)
                    if row.status in {"publishing", "review"}:
                        row.status = "review"
                        row.expires_at = datetime.now(timezone.utc) + timedelta(
                            minutes=5
                        )
                        db.commit()
            except Exception as retry_exc:
                logger.warning(
                    "AI quota recovery deferral failed: %s", type(retry_exc).__name__
                )
