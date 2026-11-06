"""Persist assignment membership and its optional notice in one transaction."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import check_user_permission, verify_token
from app.models import (
    AssignmentChannelDelivery,
    Staff,
    Visitor,
    VisitorAssignmentHistory,
    VisitorSession,
    VisitorWaitingQueue,
)
from app.services.channel_effect_delivery import (
    current_channel_staff,
    deliver_effect_record,
    valid_effects,
)
from app.services.channel_transport_lock import channel_transport_session
from app.schemas.staff_data_access import StaffDataAccess
from app.services.session_command_membership import stage_channel_effects
from app.services.visitor_mutation_access import timestamp
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE, MessageType
from app.utils.encoding import build_visitor_channel_id

logger = get_logger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stage_assignment_channel(
    db: Session,
    history: VisitorAssignmentHistory,
    *,
    send_notification: bool,
    ai_disabled: bool,
    access: StaffDataAccess | None = None,
    permission: str | None = None,
    queue_entry_id: UUID | None = None,
) -> UUID:
    if history.assigned_staff_id is None or history.session_id is None:
        raise ValueError("An assigned session is required for channel work")
    staff = (
        db.query(Staff)
        .filter(
            Staff.id == history.assigned_staff_id,
            Staff.project_id == history.project_id,
        )
        .first()
    )
    if staff is None:
        raise ValueError("Assignment staff is outside the project")
    effects = stage_channel_effects(
        db,
        project_id=history.project_id,
        visitor_id=history.visitor_id,
        previous_staff_id=history.previous_staff_id,
        target_staff_id=history.assigned_staff_id,
    )
    uid = f"{staff.id}-staff"
    authority: dict[str, JsonValue] | None = None
    if access is not None:
        claims = verify_token(access.token.get_secret_value())
        if claims is None or permission is None:
            raise ValueError("Current staff authority is required")
        visitor = db.get(Visitor, history.visitor_id)
        session = db.get(VisitorSession, history.session_id)
        queue = (
            db.get(VisitorWaitingQueue, queue_entry_id)
            if queue_entry_id
            else None
        )
        if (
            visitor is None
            or session is None
            or (
                queue_entry_id is not None
                and (
                    queue is None
                    or queue.project_id != history.project_id
                    or queue.visitor_id != history.visitor_id
                    or queue.session_id != history.session_id
                )
            )
        ):
            raise ValueError("The original reception context is required")
        authority = {
            "staff_id": str(access.staff_id),
            "role": access.role,
            "username": access.username,
            "token_version": access.token_version,
            "expires_at": claims.get("exp"),
            "permission": permission,
            "visitor_version": timestamp(visitor.updated_at).isoformat(),
            "session_version": timestamp(session.updated_at).isoformat(),
            "queue_id": str(queue.id) if queue is not None else None,
            "queue_version": timestamp(queue.updated_at).isoformat()
            if queue is not None
            else None,
        }
    payload: dict[str, JsonValue] = {}
    if send_notification and ai_disabled:
        payload = {
            "type": MessageType.STAFF_ASSIGNED,
            "content": (
                "You have been connected to customer service. "
                "Agent {0} will assist you."
            ),
            "extra": [{"uid": uid, "name": staff.name or staff.username}],
        }
    db.add(
        AssignmentChannelDelivery(
            id=history.id,
            project_id=history.project_id,
            visitor_id=history.visitor_id,
            session_id=history.session_id,
            target_staff_id=staff.id,
            channel_id=build_visitor_channel_id(history.visitor_id),
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            effects=effects,
            client_msg_no=f"assignment-{history.id}",
            from_uid=uid,
            payload=payload,
            actor_authority=authority,
            status="pending" if payload else "skipped",
        )
    )
    db.flush()
    return history.id


def _actor_is_current(db: Session, row: AssignmentChannelDelivery) -> bool:
    authority = row.actor_authority
    if authority is None:
        return True
    if set(authority) != {
        "staff_id",
        "username",
        "role",
        "token_version",
        "expires_at",
        "permission",
        "visitor_version",
        "session_version",
        "queue_id",
        "queue_version",
    }:
        return False
    expires, permission = authority["expires_at"], authority["permission"]
    if (
        type(expires) not in {int, float}
        or not isinstance(expires, (int, float))
        or expires <= utc_now().timestamp()
        or not isinstance(permission, str)
    ):
        return False
    try:
        staff_id = UUID(str(authority["staff_id"]))
    except ValueError:
        return False
    actor = (
        db.query(Staff)
        .filter(
            Staff.id == staff_id,
            Staff.project_id == row.project_id,
            Staff.deleted_at.is_(None),
            Staff.account_enabled.is_(True),
            Staff.is_active.is_(True),
        )
        .populate_existing()
        .first()
    )
    visitor = (
        db.query(Visitor)
        .filter_by(
            id=row.visitor_id,
            project_id=row.project_id,
        )
        .populate_existing()
        .first()
    )
    session = (
        db.query(VisitorSession)
        .filter_by(
            id=row.session_id,
            project_id=row.project_id,
            visitor_id=row.visitor_id,
        )
        .populate_existing()
        .first()
    )
    if (
        visitor is None
        or session is None
        or timestamp(visitor.updated_at).isoformat()
        != authority["visitor_version"]
        or timestamp(session.updated_at).isoformat()
        != authority["session_version"]
    ):
        return False
    if authority["queue_id"] is not None:
        try:
            entry_id = UUID(str(authority["queue_id"]))
        except ValueError:
            return False
        queue = (
            db.query(VisitorWaitingQueue)
            .filter_by(
                id=entry_id,
                project_id=row.project_id,
                visitor_id=row.visitor_id,
                session_id=row.session_id,
                status="assigned",
                assigned_staff_id=row.target_staff_id,
            )
            .populate_existing()
            .first()
        )
        if (
            queue is None
            or timestamp(queue.updated_at).isoformat()
            != authority["queue_version"]
        ):
            return False
    return actor is not None and (
        actor.role == authority["role"]
        and actor.username == authority["username"]
        and actor.token_version == authority["token_version"]
        and check_user_permission(db, actor, permission)
    )


def _notice_is_current(db: Session, row: AssignmentChannelDelivery) -> bool:
    if (
        not valid_effects(row)
        or not _actor_is_current(db, row)
        or row.target_staff_id is None
        or row.client_msg_no != f"assignment-{row.id}"
        or row.from_uid != f"{row.target_staff_id}-staff"
        or row.payload.get("type") != MessageType.STAFF_ASSIGNED
        or current_channel_staff(db, row.project_id, row.visitor_id)
        != row.target_staff_id
    ):
        return False
    session = (
        db.query(VisitorSession.id)
        .filter(
            VisitorSession.id == row.session_id,
            VisitorSession.visitor_id == row.visitor_id,
            VisitorSession.project_id == row.project_id,
            VisitorSession.staff_id == row.target_staff_id,
            VisitorSession.status == "open",
        )
        .first()
    )
    visitor = (
        db.query(Visitor.id)
        .filter(
            Visitor.id == row.visitor_id,
            Visitor.project_id == row.project_id,
            Visitor.ai_disabled.is_(True),
        )
        .first()
    )
    staff = (
        db.query(Staff.id)
        .filter(
            Staff.id == row.target_staff_id,
            Staff.project_id == row.project_id,
            Staff.deleted_at.is_(None),
            Staff.account_enabled.is_(True),
            Staff.is_active.is_(True),
            Staff.service_paused.is_(False),
            Staff.role.in_(["admin", "user"]),
        )
        .first()
    )
    return session is not None and visitor is not None and staff is not None


async def _deliver_notice(db: Session, operation_id: UUID) -> str:
    row = (
        db.query(AssignmentChannelDelivery)
        .filter_by(
            id=operation_id,
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if row is None:
        db.rollback()
        return "missing"
    if row.status in {"sent", "skipped"}:
        state = row.status
        db.rollback()
        return state
    if not _notice_is_current(db, row):
        row.status, row.last_error = "skipped", "RECEPTION_CHANGED"
        row.updated_at = utc_now()
        db.commit()
        return "skipped"
    if row.effects_status == "skipped":
        row.status, row.last_error = "skipped", "CHANNEL_TARGET_UNAVAILABLE"
        row.updated_at = utc_now()
        db.commit()
        return "skipped"
    if row.effects_status != "done":
        state = row.status
        db.rollback()
        return state
    reconcile = row.status in {"sending", "unknown"}
    if reconcile and timestamp(row.updated_at) > timestamp(
        utc_now() - timedelta(seconds=30)
    ):
        state = row.status
        db.rollback()
        return state
    channel_id, channel_type = row.channel_id, row.channel_type
    client_msg_no, from_uid = row.client_msg_no, row.from_uid
    payload = dict(row.payload)
    row.status = "unknown" if reconcile else "sending"
    row.updated_at = utc_now()
    db.commit()
    confirmed = False
    error = "IM_RECEIPT_UNKNOWN"
    try:
        if reconcile:
            message = await wukongim_client.get_message_by_client_msg_no(
                channel_id=channel_id,
                channel_type=channel_type,
                client_msg_no=client_msg_no,
                raise_on_error=True,
            )
            confirmed = message is not None and (
                message.client_msg_no == client_msg_no
                and message.channel_id == channel_id
                and message.channel_type == channel_type
                and message.from_uid == from_uid
                and message.payload == payload
            )
        else:
            response = await wukongim_client.send_message(
                channel_id=channel_id,
                channel_type=channel_type,
                client_msg_no=client_msg_no,
                from_uid=from_uid,
                payload=payload,
            )
            confirmed = (
                response is not None
                and response.client_msg_no == client_msg_no
            )
    except Exception as exc:
        error = type(exc).__name__[:80]
        logger.warning("Assignment notice outcome unknown: %s", error)
    row = (
        db.query(AssignmentChannelDelivery)
        .filter_by(
            id=operation_id,
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if row is None:
        db.rollback()
        return "missing"
    row.status = "sent" if confirmed else "unknown"
    row.last_error = None if confirmed else error
    row.updated_at = utc_now()
    db.commit()
    return "sent" if confirmed else "unknown"


async def deliver_assignment_channel(db: Session, operation_id: UUID) -> str:
    await deliver_effect_record(db, AssignmentChannelDelivery, operation_id)
    if not wukongim_client.enabled:
        return "pending"
    identity = (
        db.query(
            AssignmentChannelDelivery.project_id,
            AssignmentChannelDelivery.visitor_id,
        )
        .filter_by(id=operation_id)
        .first()
    )
    db.rollback()
    if identity is None:
        return "missing"
    try:
        with channel_transport_session(
            db,
            identity.project_id,
            identity.visitor_id,
        ) as work:
            if work is None:
                return "pending"
            return await _deliver_notice(work, operation_id)
    finally:
        db.expire_all()


async def recover_assignment_channels(db: Session, limit: int = 20) -> int:
    if not 1 <= limit <= 50:
        raise ValueError("Recovery limit must be between 1 and 50")
    if not wukongim_client.enabled:
        return 0
    cutoff = utc_now() - timedelta(seconds=30)
    ids = [
        row[0]
        for row in db.query(AssignmentChannelDelivery.id)
        .filter(
            or_(
                AssignmentChannelDelivery.effects_status == "pending",
                and_(
                    AssignmentChannelDelivery.effects_status == "unknown",
                    AssignmentChannelDelivery.effects_updated_at <= cutoff,
                ),
                and_(
                    AssignmentChannelDelivery.effects_status.in_(
                        ["done", "skipped"]
                    ),
                    or_(
                        AssignmentChannelDelivery.status == "pending",
                        and_(
                            AssignmentChannelDelivery.status.in_(
                                ["sending", "unknown"]
                            ),
                            AssignmentChannelDelivery.updated_at <= cutoff,
                        ),
                    ),
                ),
            ),
        )
        .order_by(
            case(
                (
                    AssignmentChannelDelivery.effects_status.in_(
                        ["pending", "unknown"]
                    ),
                    AssignmentChannelDelivery.effects_updated_at,
                ),
                else_=AssignmentChannelDelivery.updated_at,
            ),
            AssignmentChannelDelivery.id,
        )
        .limit(limit)
        .all()
    ]
    db.rollback()
    for operation_id in ids:
        try:
            await deliver_assignment_channel(db, operation_id)
        except Exception as exc:
            db.rollback()
            logger.error(
                "Assignment channel recovery failed: %s", type(exc).__name__
            )
            # A repeatedly failing item must yield to later pending work.
            try:
                row = (
                    db.query(AssignmentChannelDelivery)
                    .filter_by(
                        id=operation_id,
                    )
                    .populate_existing()
                    .with_for_update(skip_locked=True)
                    .first()
                )
                if row is None:
                    db.rollback()
                    continue
                now = utc_now()
                if row.effects_status in {"pending", "unknown"}:
                    row.effects_status = "unknown"
                    row.effects_last_error = "RECOVERY_FAILED"
                    row.effects_updated_at = now
                elif row.status in {"pending", "sending", "unknown"}:
                    # Delivery may have reached IM before the local commit.
                    row.status, row.last_error = "unknown", "RECOVERY_FAILED"
                    row.updated_at = now
                db.commit()
            except Exception as mark_exc:
                db.rollback()
                logger.error(
                    "Assignment recovery cooldown failed: %s",
                    type(mark_exc).__name__,
                )
    return len(ids)
