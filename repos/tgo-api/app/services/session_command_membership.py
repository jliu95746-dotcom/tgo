"""Stage channel membership in the manual session command's transaction."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    ChannelMember,
    SessionCommandDelivery,
)
from app.services.channel_effect_delivery import deliver_effect_record
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE, MEMBER_TYPE_STAFF
from app.utils.encoding import build_visitor_channel_id

logger = get_logger("services.session_command_membership")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stage_channel_effects(
    db: Session,
    *,
    project_id: UUID,
    visitor_id: UUID,
    previous_staff_id: UUID | None,
    target_staff_id: UUID | None,
) -> list[dict[str, str]]:
    """Commit member rows and replayable IM actions with the session change."""
    channel_id = build_visitor_channel_id(visitor_id)
    historical_members = (
        db.query(ChannelMember)
        .filter(
            ChannelMember.project_id == project_id,
            ChannelMember.channel_id == channel_id,
            ChannelMember.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE,
            ChannelMember.member_type == MEMBER_TYPE_STAFF,
        )
        .with_for_update()
        .all()
    )
    active_members = [m for m in historical_members if m.deleted_at is None]
    removed_ids = {
        member.member_id
        for member in historical_members
        if member.member_id != target_staff_id
    }
    if previous_staff_id is not None and previous_staff_id != target_staff_id:
        removed_ids.add(previous_staff_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for member in active_members:
        if member.member_id != target_staff_id:
            member.deleted_at = now

    if target_staff_id is not None and not any(
        member.member_id == target_staff_id for member in active_members
    ):
        db.add(
            ChannelMember(
                project_id=project_id,
                channel_id=channel_id,
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                member_id=target_staff_id,
                member_type=MEMBER_TYPE_STAFF,
            )
        )

    effects: list[dict[str, str]] = []
    for staff_id in sorted(removed_ids, key=str):
        effects.append(
            {"kind": "remove_subscriber", "staff_id": str(staff_id)}
        )
        effects.append(
            {"kind": "delete_conversation", "staff_id": str(staff_id)}
        )
    if target_staff_id is not None:
        effects.append(
            {"kind": "add_subscriber", "staff_id": str(target_staff_id)}
        )
    db.flush()
    return effects


async def deliver_channel_effects(db: Session, operation_id: UUID) -> str:
    return await deliver_effect_record(
        db, SessionCommandDelivery, operation_id
    )


async def recover_channel_effects(db: Session, limit: int = 20) -> int:
    if not wukongim_client.enabled:
        return 0
    cutoff = utc_now() - timedelta(seconds=30)
    ids = [
        item[0]
        for item in db.query(SessionCommandDelivery.id)
        .filter(
            or_(
                SessionCommandDelivery.effects_status == "pending",
                (
                    (SessionCommandDelivery.effects_status == "unknown")
                    & (SessionCommandDelivery.effects_updated_at <= cutoff)
                ),
            )
        )
        .order_by(
            SessionCommandDelivery.effects_updated_at,
            SessionCommandDelivery.created_at,
        )
        .limit(limit)
        .all()
    ]
    db.rollback()
    processed = 0
    for operation_id in ids:
        try:
            await deliver_channel_effects(db, operation_id)
            processed += 1
        except Exception as exc:
            db.rollback()
            logger.error(
                "Session channel effect recovery failed",
                extra={
                    "operation_id": str(operation_id),
                    "error_type": type(exc).__name__,
                },
            )
            try:
                row = (
                    db.query(SessionCommandDelivery)
                    .filter(SessionCommandDelivery.id == operation_id)
                    .with_for_update(skip_locked=True)
                    .first()
                )
                if row is not None and row.effects_status in {
                    "pending",
                    "unknown",
                }:
                    row.effects_status = "unknown"
                    row.effects_last_error = "RECOVERY_FAILED"
                    row.effects_updated_at = utc_now()
                    db.commit()
                else:
                    db.rollback()
            except Exception as mark_exc:
                db.rollback()
                logger.error(
                    "Session channel effect cooldown update failed",
                    extra={
                        "operation_id": str(operation_id),
                        "error_type": type(mark_exc).__name__,
                    },
                )
    return processed
