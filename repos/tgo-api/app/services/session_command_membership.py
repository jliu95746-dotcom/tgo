"""Stage a manual session command's channel membership in its business transaction."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    ChannelMember, Platform, Project, SessionCommandDelivery,
    Staff, Visitor, VisitorSession,
)
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
    active_members = (
        db.query(ChannelMember)
        .filter(
            ChannelMember.project_id == project_id,
            ChannelMember.channel_id == channel_id,
            ChannelMember.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE,
            ChannelMember.member_type == MEMBER_TYPE_STAFF,
            ChannelMember.deleted_at.is_(None),
        )
        .with_for_update()
        .all()
    )
    removed_ids = {member.member_id for member in active_members
                   if member.member_id != target_staff_id}
    if previous_staff_id is not None and previous_staff_id != target_staff_id:
        removed_ids.add(previous_staff_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for member in active_members:
        if member.member_id != target_staff_id:
            member.deleted_at = now

    if target_staff_id is not None and not any(
        member.member_id == target_staff_id for member in active_members
    ):
        db.add(ChannelMember(
            project_id=project_id,
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            member_id=target_staff_id,
            member_type=MEMBER_TYPE_STAFF,
        ))

    effects: list[dict[str, str]] = []
    for staff_id in sorted(removed_ids, key=str):
        effects.append({"kind": "remove_subscriber", "staff_id": str(staff_id)})
        effects.append({"kind": "delete_conversation", "staff_id": str(staff_id)})
    if target_staff_id is not None:
        effects.append({"kind": "add_subscriber", "staff_id": str(target_staff_id)})
    db.flush()
    return effects


def _current_staff(db: Session, row: SessionCommandDelivery) -> UUID | None:
    project = (
        db.query(Project.id)
        .filter(Project.id == row.project_id, Project.deleted_at.is_(None))
        .with_for_update(read=True)
        .first()
    )
    if project is None:
        return None
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.id == row.visitor_id,
            Visitor.project_id == row.project_id,
            Visitor.deleted_at.is_(None),
            Visitor.access_retired_at.is_(None),
        )
        .with_for_update()
        .first()
    )
    if visitor is None:
        return None
    platform = (
        db.query(Platform.id)
        .filter(
            Platform.id == visitor.platform_id,
            Platform.project_id == row.project_id,
            Platform.deleted_at.is_(None),
            Platform.is_active.is_(True),
        )
        .with_for_update(read=True)
        .first()
    )
    if platform is None:
        return None
    latest = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.project_id == row.project_id,
            VisitorSession.visitor_id == row.visitor_id,
            VisitorSession.status == "open",
        )
        .order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc())
        .first()
    )
    return latest.staff_id if latest is not None else None


def _valid_effects(row: SessionCommandDelivery) -> bool:
    if (row.channel_id != build_visitor_channel_id(row.visitor_id)
            or row.channel_type != CHANNEL_TYPE_CUSTOMER_SERVICE
            or not isinstance(row.effects, list)):
        return False
    for effect in row.effects:
        if not isinstance(effect, dict) or set(effect) != {"kind", "staff_id"}:
            return False
        if effect["kind"] not in {
            "remove_subscriber", "delete_conversation", "add_subscriber",
        }:
            return False
        try:
            UUID(effect["staff_id"])
        except (ValueError, TypeError, AttributeError):
            return False
    return True


async def deliver_channel_effects(db: Session, operation_id: UUID) -> str:
    """Replay idempotent IM changes only when they agree with current ownership."""
    row = (
        db.query(SessionCommandDelivery)
        .filter(SessionCommandDelivery.id == operation_id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if row is None:
        db.rollback()
        return "missing"
    if row.effects_status in {"done", "skipped"}:
        status = row.effects_status
        db.rollback()
        return status
    if not wukongim_client.enabled:
        status = row.effects_status
        db.rollback()
        return status
    if not _valid_effects(row):
        row.effects_status = "unknown"
        row.effects_last_error = "INVALID_EFFECTS"
        row.effects_updated_at = utc_now()
        db.commit()
        return "unknown"

    current_staff_id = _current_staff(db, row)
    row.effects_attempts += 1
    performed = False
    target_skipped = False
    try:
        for effect in row.effects:
            staff_id = UUID(effect["staff_id"])
            staff_uid = f"{staff_id}-staff"
            kind = effect["kind"]
            if kind in {"remove_subscriber", "delete_conversation"}:
                if staff_id == current_staff_id:
                    continue
                if kind == "remove_subscriber":
                    await wukongim_client.remove_channel_subscribers(
                        channel_id=row.channel_id,
                        channel_type=row.channel_type,
                        subscribers=[staff_uid],
                    )
                else:
                    await wukongim_client.delete_conversation(
                        uid=staff_uid,
                        channel_id=row.channel_id,
                        channel_type=row.channel_type,
                    )
                performed = True
                continue
            if staff_id != current_staff_id:
                target_skipped = True
                continue
            target = (
                db.query(Staff)
                .filter(
                    Staff.id == staff_id,
                    Staff.project_id == row.project_id,
                    Staff.deleted_at.is_(None),
                    Staff.account_enabled.is_(True),
                    Staff.is_active.is_(True),
                    Staff.service_paused.is_(False),
                    Staff.role.in_(["admin", "user"]),
                )
                .first()
            )
            if target is None:
                target_skipped = True
                continue
            await wukongim_client.add_channel_subscribers(
                channel_id=row.channel_id,
                channel_type=row.channel_type,
                subscribers=[staff_uid],
            )
            performed = True
    except Exception as exc:
        logger.warning(
            "Session channel effect outcome unknown",
            extra={"operation_id": str(operation_id), "error_type": type(exc).__name__},
        )
        row.effects_status = "unknown"
        row.effects_last_error = type(exc).__name__[:80]
    else:
        row.effects_status = (
            "skipped" if target_skipped else "done" if performed else "skipped"
        )
        row.effects_last_error = (
            "TARGET_NO_LONGER_AVAILABLE" if target_skipped else None
        )
    row.effects_updated_at = utc_now()
    status = row.effects_status
    db.commit()
    return status


async def recover_channel_effects(db: Session, limit: int = 20) -> int:
    if not wukongim_client.enabled:
        return 0
    cutoff = utc_now() - timedelta(seconds=30)
    ids = [
        item[0]
        for item in db.query(SessionCommandDelivery.id)
        .filter(or_(
            SessionCommandDelivery.effects_status == "pending",
            (
                (SessionCommandDelivery.effects_status == "unknown")
                & (SessionCommandDelivery.effects_updated_at <= cutoff)
            ),
        ))
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
                extra={"operation_id": str(operation_id), "error_type": type(exc).__name__},
            )
            try:
                row = (
                    db.query(SessionCommandDelivery)
                    .filter(SessionCommandDelivery.id == operation_id)
                    .with_for_update(skip_locked=True)
                    .first()
                )
                if row is not None and row.effects_status in {"pending", "unknown"}:
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
