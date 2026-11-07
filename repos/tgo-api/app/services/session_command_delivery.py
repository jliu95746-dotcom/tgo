"""Persist manual session notices and reconcile uncertain WuKongIM outcomes."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.exceptions import TGOAPIException
from app.core.security import check_user_permission, verify_token
from app.models import (
    Platform,
    Project,
    SessionCommandDelivery,
    Staff,
    Visitor,
    VisitorSession,
)
from app.services.staff_session_access import StaffSessionScope
from app.services.session_command_membership import stage_channel_effects
from app.services.visitor_mutation_access import timestamp
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id

logger = get_logger("services.session_command_delivery")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def prepare_operation_id(db: Session, requested_id: UUID | None) -> UUID:
    """Serialize retries of a caller-owned ID before changing a session."""
    if requested_id is None:
        return uuid4()
    if requested_id.version != 4:
        raise TGOAPIException(
            "操作编号格式无效", "INVALID_OPERATION_ID", status_code=422
        )
    if db.get_bind().dialect.name == "postgresql":
        lock_key = int.from_bytes(requested_id.bytes[:8], "big", signed=True)
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key}
        )
    exists = (
        db.query(SessionCommandDelivery.id)
        .filter(SessionCommandDelivery.id == requested_id)
        .first()
    )
    if exists is not None:
        raise TGOAPIException(
            "原操作已提交，请查询回执", "SESSION_OPERATION_EXISTS", status_code=409
        )
    return requested_id


def original_token_expiry(scope: StaffSessionScope) -> datetime:
    claims = verify_token(scope.access.token.get_secret_value())
    expiry = claims.get("exp") if claims is not None else None
    if type(expiry) is not int:
        raise ValueError("Original staff token is missing an expiry")
    return datetime.fromtimestamp(expiry, timezone.utc)


def record_close(
    db: Session,
    scope: StaffSessionScope,
    session: VisitorSession,
    actor: Staff,
    operation_id: UUID,
) -> SessionCommandDelivery:
    staff_uid = f"{actor.id}-staff"
    name = actor.name or actor.nickname or actor.username
    payload = {
        "type": 1001,
        "content": "Session ended. Agent {0} has completed the service.",
        "extra": [{"uid": staff_uid, "name": name}],
    }
    effects = stage_channel_effects(
        db,
        project_id=scope.access.project_id,
        visitor_id=scope.visitor_id,
        previous_staff_id=scope.original_staff_id,
        target_staff_id=None,
    )
    row = SessionCommandDelivery(
        id=operation_id,
        project_id=scope.access.project_id,
        visitor_id=scope.visitor_id,
        session_id=scope.session_id,
        actor_staff_id=scope.access.staff_id,
        from_staff_id=scope.original_staff_id,
        kind="close",
        session_version=timestamp(session.updated_at),
        actor_role=scope.access.role,
        actor_token_version=scope.access.token_version,
        actor_token_expires_at=original_token_expiry(scope),
        client_msg_no=f"session-command-{operation_id.hex}",
        channel_id=build_visitor_channel_id(scope.visitor_id),
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        from_uid=staff_uid,
        payload=payload,
        status="pending",
        effects=effects,
        effects_status="pending" if effects else "done",
    )
    db.add(row)
    db.flush()
    return row


def record_transfer(
    db: Session,
    scope: StaffSessionScope,
    session: VisitorSession,
    from_staff: Staff | None,
    to_staff: Staff,
    operation_id: UUID,
) -> SessionCommandDelivery:
    from_id = scope.original_staff_id or scope.access.staff_id
    from_uid = f"{from_id}-staff"
    to_uid = f"{to_staff.id}-staff"
    from_name = (
        (from_staff.name or from_staff.username)
        if from_staff is not None
        else scope.access.username
    )
    payload = {
        "type": 1002,
        "content": (
            'Session transferred. Agent {0} has transferred you to Agent {1}.'
        ),
        "extra": [
            {"uid": from_uid, "name": from_name},
            {"uid": to_uid, "name": to_staff.name or to_staff.username},
        ],
    }
    effects = stage_channel_effects(
        db,
        project_id=scope.access.project_id,
        visitor_id=scope.visitor_id,
        previous_staff_id=scope.original_staff_id,
        target_staff_id=to_staff.id,
    )
    row = SessionCommandDelivery(
        id=operation_id,
        project_id=scope.access.project_id,
        visitor_id=scope.visitor_id,
        session_id=scope.session_id,
        actor_staff_id=scope.access.staff_id,
        from_staff_id=scope.original_staff_id,
        to_staff_id=to_staff.id,
        kind="transfer",
        session_version=timestamp(session.updated_at),
        actor_role=scope.access.role,
        actor_token_version=scope.access.token_version,
        actor_token_expires_at=original_token_expiry(scope),
        client_msg_no=f"session-command-{operation_id.hex}",
        channel_id=build_visitor_channel_id(scope.visitor_id),
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        from_uid=to_uid,
        payload=payload,
        status="pending",
        effects=effects,
        effects_status="pending" if effects else "done",
    )
    db.add(row)
    db.flush()
    return row


def _still_current(db: Session, row: SessionCommandDelivery) -> bool:
    if timestamp(row.actor_token_expires_at) <= timestamp(utc_now()):
        return False
    project = (
        db.query(Project.id)
        .filter(Project.id == row.project_id, Project.deleted_at.is_(None))
        .first()
    )
    actor = (
        db.query(Staff)
        .filter(
            Staff.id == row.actor_staff_id,
            Staff.project_id == row.project_id,
            Staff.deleted_at.is_(None),
            Staff.account_enabled.is_(True),
            Staff.is_active.is_(True),
        )
        .first()
    )
    if (
        project is None
        or actor is None
        or actor.role != row.actor_role
        or actor.token_version != row.actor_token_version
        or not check_user_permission(db, actor, "visitors:update")
    ):
        return False
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.id == row.visitor_id,
            Visitor.project_id == row.project_id,
            Visitor.deleted_at.is_(None),
            Visitor.access_retired_at.is_(None),
        )
        .first()
    )
    if visitor is None:
        return False
    platform = (
        db.query(Platform.id)
        .filter(
            Platform.id == visitor.platform_id,
            Platform.project_id == row.project_id,
            Platform.deleted_at.is_(None),
            Platform.is_active.is_(True),
        )
        .first()
    )
    session = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.id == row.session_id,
            VisitorSession.project_id == row.project_id,
            VisitorSession.visitor_id == row.visitor_id,
        )
        .first()
    )
    if (
        platform is None
        or session is None
        or timestamp(session.updated_at) != timestamp(row.session_version)
    ):
        return False
    latest_open = (
        db.query(VisitorSession.id)
        .filter(
            VisitorSession.project_id == row.project_id,
            VisitorSession.visitor_id == row.visitor_id,
            VisitorSession.status == "open",
        )
        .order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc())
        .first()
    )
    if row.kind == "close":
        return (
            latest_open is None
            and session.status == "closed"
            and session.staff_id == row.from_staff_id
        )
    if row.kind != "transfer" or session.status != "open":
        return False
    if latest_open is None or latest_open[0] != row.session_id:
        return False
    target = (
        db.query(Staff)
        .filter(
            Staff.id == row.to_staff_id,
            Staff.project_id == row.project_id,
            Staff.deleted_at.is_(None),
            Staff.account_enabled.is_(True),
            Staff.is_active.is_(True),
            Staff.service_paused.is_(False),
            Staff.role.in_(["admin", "user"]),
        )
        .first()
    )
    return target is not None and session.staff_id == row.to_staff_id


async def deliver_session_command(db: Session, operation_id: UUID) -> str:
    (
        'Send once; uncertain attempts are reconciled but never '
        'automatically resent.'
    )
    row = (
        db.query(SessionCommandDelivery)
        .filter(SessionCommandDelivery.id == operation_id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if row is None:
        db.rollback()
        return "missing"
    if row.status in {"sent", "skipped"}:
        status = row.status
        db.rollback()
        return status
    if not wukongim_client.enabled:
        status = row.status
        db.rollback()
        return status
    if row.status in {"sending", "unknown"}:
        db.commit()
        return await reconcile_session_command(db, operation_id)
    if not _still_current(db, row):
        row.status = "skipped"
        row.last_error = "AUTHORITY_OR_SESSION_CHANGED"
        db.commit()
        return "skipped"
    row.status = "sending"
    row.attempts += 1
    row.updated_at = utc_now()
    payload = row.payload
    from_uid = row.from_uid
    channel_id = row.channel_id
    channel_type = row.channel_type
    client_msg_no = row.client_msg_no
    db.commit()
    try:
        result = await wukongim_client.send_message(
            payload=payload,
            from_uid=from_uid,
            channel_id=channel_id,
            channel_type=channel_type,
            client_msg_no=client_msg_no,
        )
    except Exception as exc:
        logger.warning(
            "Session notice outcome unknown: %s", type(exc).__name__
        )
        result = None
    current = (
        db.query(SessionCommandDelivery)
        .filter(SessionCommandDelivery.id == operation_id)
        .with_for_update()
        .one()
    )
    if result is None:
        current.status = "unknown"
        current.last_error = "IM_RECEIPT_UNKNOWN"
    else:
        current.status = "sent"
        current.message_id = str(result.message_id)
        current.sent_at = utc_now()
        current.last_error = None
    current.updated_at = utc_now()
    status = current.status
    db.commit()
    return status


async def reconcile_session_command(db: Session, operation_id: UUID) -> str:
    row = (
        db.query(SessionCommandDelivery)
        .filter(SessionCommandDelivery.id == operation_id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if row is None:
        db.rollback()
        return "missing"
    if row.status not in {"sending", "unknown"}:
        status = row.status
        db.rollback()
        return status
    if not wukongim_client.enabled:
        status = row.status
        db.rollback()
        return status
    try:
        message = await wukongim_client.get_message_by_client_msg_no(
            channel_id=row.channel_id,
            channel_type=row.channel_type,
            client_msg_no=row.client_msg_no,
            raise_on_error=True,
        )
        if message is not None and (
            message.from_uid != row.from_uid or message.payload != row.payload
        ):
            row.last_error = "IM_MESSAGE_IDENTITY_CONFLICT"
        elif message is not None:
            row.status = "sent"
            row.message_id = str(message.message_id)
            row.sent_at = utc_now()
            row.last_error = None
        else:
            row.last_error = "IM_RECEIPT_UNKNOWN"
    except Exception as exc:
        logger.warning("Session notice lookup failed: %s", type(exc).__name__)
        row.last_error = "IM_LOOKUP_FAILED"
    if row.status != "sent":
        row.status = "unknown"
    row.updated_at = utc_now()
    status = row.status
    db.commit()
    return status


async def recover_session_commands(db: Session, limit: int = 20) -> int:
    if not wukongim_client.enabled:
        return 0
    cutoff = utc_now() - timedelta(seconds=30)
    pending_ready = (SessionCommandDelivery.status == "pending") & or_(
        SessionCommandDelivery.last_error.is_(None),
        SessionCommandDelivery.last_error != "RECOVERY_FAILED",
        SessionCommandDelivery.updated_at <= cutoff,
    )
    ids = [
        item[0]
        for item in db.query(SessionCommandDelivery.id)
        .filter(
            or_(
                pending_ready,
                (
                    SessionCommandDelivery.status.in_(["sending", "unknown"])
                    & (SessionCommandDelivery.updated_at <= cutoff)
                ),
            )
        )
        .order_by(
            SessionCommandDelivery.updated_at,
            SessionCommandDelivery.created_at,
        )
        .limit(limit)
        .all()
    ]
    db.rollback()
    processed = 0
    for operation_id in ids:
        try:
            await deliver_session_command(db, operation_id)
            processed += 1
        except Exception as exc:
            db.rollback()
            logger.error(
                "Session command recovery failed",
                extra={
                    "operation_id": str(operation_id),
                    "error_type": type(exc).__name__,
                },
            )
            row = (
                db.query(SessionCommandDelivery)
                .filter(SessionCommandDelivery.id == operation_id)
                .with_for_update()
                .first()
            )
            if row is not None and row.status in {
                "pending",
                "sending",
                "unknown",
            }:
                if row.status == "pending" or row.last_error is None:
                    row.last_error = "RECOVERY_FAILED"
                row.updated_at = utc_now()
                db.commit()
            else:
                db.rollback()
    return processed
