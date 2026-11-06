"""Shared, serialized delivery for manual and automatic channel changes."""

from datetime import datetime, timezone
from typing import TypeAlias, cast
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    AssignmentChannelDelivery,
    Platform,
    Project,
    SessionCommandDelivery,
    Staff,
    Visitor,
    VisitorSession,
)
from app.services.channel_transport_lock import channel_transport_session
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id

DeliveryRow: TypeAlias = AssignmentChannelDelivery | SessionCommandDelivery
DeliveryModel: TypeAlias = (
    type[AssignmentChannelDelivery] | type[SessionCommandDelivery]
)
logger = get_logger(__name__)


def current_channel_staff(
    db: Session,
    project_id: UUID,
    visitor_id: UUID,
) -> UUID | None:
    project = (
        db.query(Project.id)
        .filter(
            Project.id == project_id,
            Project.deleted_at.is_(None),
        )
        .first()
    )
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.id == visitor_id,
            Visitor.project_id == project_id,
            Visitor.deleted_at.is_(None),
            Visitor.access_retired_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if project is None or visitor is None:
        return None
    platform = (
        db.query(Platform.id)
        .filter(
            Platform.id == visitor.platform_id,
            Platform.project_id == project_id,
            Platform.deleted_at.is_(None),
            Platform.is_active.is_(True),
        )
        .first()
    )
    if platform is None:
        return None
    latest = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.project_id == project_id,
            VisitorSession.visitor_id == visitor_id,
        )
        .order_by(
            VisitorSession.created_at.desc(),
            VisitorSession.id.desc(),
        )
        .populate_existing()
        .first()
    )
    return (
        latest.staff_id
        if (
            latest is not None
            and latest.status == "open"
        # Older owned sessions legitimately have no platform_id. The visitor's
        # current project/platform above remains the channel authority.
        and latest.platform_id in {None, visitor.platform_id}
        )
        else None
    )


def valid_effects(row: DeliveryRow) -> bool:
    if (
        row.channel_id != build_visitor_channel_id(row.visitor_id)
        or row.channel_type != CHANNEL_TYPE_CUSTOMER_SERVICE
        or not isinstance(row.effects, list)
    ):
        return False
    for effect in row.effects:
        if not isinstance(effect, dict) or set(effect) != {"kind", "staff_id"}:
            return False
        if effect["kind"] not in {
            "remove_subscriber",
            "delete_conversation",
            "add_subscriber",
        }:
            return False
        try:
            UUID(effect["staff_id"])
        except (ValueError, TypeError, AttributeError):
            return False
    return True


async def _remove(channel_id: str, channel_type: int, staff_id: UUID) -> None:
    uid = f"{staff_id}-staff"
    await wukongim_client.remove_channel_subscribers(
        channel_id=channel_id,
        channel_type=channel_type,
        subscribers=[uid],
    )
    await wukongim_client.delete_conversation(
        uid=uid,
        channel_id=channel_id,
        channel_type=channel_type,
    )


def eligible_staff(db: Session, project_id: UUID, staff_id: UUID) -> bool:
    return (
        db.query(Staff.id)
        .filter(
            Staff.id == staff_id,
            Staff.project_id == project_id,
            Staff.deleted_at.is_(None),
            Staff.account_enabled.is_(True),
            Staff.is_active.is_(True),
            Staff.service_paused.is_(False),
            Staff.role.in_(["admin", "user"]),
        )
        .first()
        is not None
    )


async def _deliver(
    db: Session,
    model: DeliveryModel,
    operation_id: UUID,
) -> str:
    row = cast(
        DeliveryRow | None,
        db.query(model)
        .filter(
            model.id == operation_id,
        )
        .populate_existing()
        .with_for_update(skip_locked=True)
        .first(),
    )
    if row is None:
        db.rollback()
        return "missing"
    if (
        row.effects_status in {"done", "skipped"}
        or not wukongim_client.enabled
    ):
        state = row.effects_status
        db.rollback()
        return state
    if not valid_effects(row):
        row.effects_status = "unknown"
        row.effects_last_error = "INVALID_EFFECTS"
        row.effects_updated_at = datetime.now(timezone.utc)
        db.commit()
        return "unknown"
    effects = [dict(item) for item in row.effects]
    project_id, visitor_id = row.project_id, row.visitor_id
    channel_id, channel_type = row.channel_id, row.channel_type
    was_uncertain = row.effects_status == "unknown"
    row.effects_status = "unknown"
    row.effects_attempts += 1
    row.effects_last_error = "DELIVERY_IN_PROGRESS"
    row.effects_updated_at = datetime.now(timezone.utc)
    db.commit()
    performed = target_skipped = False
    error: str | None = None
    try:
        for effect in effects:
            current = current_channel_staff(db, project_id, visitor_id)
            staff_id, kind = UUID(effect["staff_id"]), effect["kind"]
            target = False
            if kind == "add_subscriber" and staff_id == current:
                target = eligible_staff(db, project_id, staff_id)
            db.rollback()
            if kind != "add_subscriber":
                if staff_id == current:
                    continue
                if kind == "remove_subscriber":
                    await wukongim_client.remove_channel_subscribers(
                        channel_id=channel_id,
                        channel_type=channel_type,
                        subscribers=[f"{staff_id}-staff"],
                    )
                else:
                    await wukongim_client.delete_conversation(
                        uid=f"{staff_id}-staff",
                        channel_id=channel_id,
                        channel_type=channel_type,
                    )
                performed = True
                continue
            if staff_id != current or not target:
                target_skipped = True
                if was_uncertain:
                    await _remove(channel_id, channel_type, staff_id)
                continue
            await wukongim_client.add_channel_subscribers(
                channel_id=channel_id,
                channel_type=channel_type,
                subscribers=[f"{staff_id}-staff"],
            )
            # If reception changed during the HTTP call, undo that stale add.
            latest = current_channel_staff(db, project_id, visitor_id)
            still_eligible = eligible_staff(db, project_id, staff_id)
            db.rollback()
            if latest != staff_id or not still_eligible:
                await _remove(channel_id, channel_type, staff_id)
                target_skipped = True
            performed = True
    except Exception as exc:
        error = type(exc).__name__[:80]
        logger.warning("Channel delivery outcome unknown: %s", error)
    current_row = cast(
        DeliveryRow | None,
        db.query(model)
        .filter(
            model.id == operation_id,
        )
        .populate_existing()
        .with_for_update()
        .first(),
    )
    if current_row is None:
        db.rollback()
        return "missing"
    current_row.effects_status = (
        "unknown"
        if error
        else "skipped"
        if target_skipped
        else "done"
        if performed
        else "skipped"
    )
    current_row.effects_last_error = error or (
        "TARGET_NO_LONGER_AVAILABLE" if target_skipped else None
    )
    current_row.effects_updated_at = datetime.now(timezone.utc)
    status = current_row.effects_status
    db.commit()
    return status


async def deliver_effect_record(
    db: Session,
    model: DeliveryModel,
    operation_id: UUID,
) -> str:
    if db.new or db.dirty or db.deleted:
        raise ValueError("Commit business changes before channel delivery")
    identity = (
        db.query(model.project_id, model.visitor_id)
        .filter(
            model.id == operation_id,
        )
        .first()
    )
    if identity is None:
        db.rollback()
        return "missing"
    project_id, visitor_id = identity.project_id, identity.visitor_id
    db.rollback()
    try:
        with channel_transport_session(db, project_id, visitor_id) as work:
            if work is None:
                return "pending"
            return await _deliver(work, model, operation_id)
    finally:
        db.expire_all()
