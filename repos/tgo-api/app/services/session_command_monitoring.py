"""Find stalled session commands without exposing customer messages."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import SessionCommandDelivery
from app.models.billing import BillingAudit
from app.models.platform_operator import PlatformOperator
from app.schemas.session_command_monitoring import (
    SessionCommandAlertItem,
    SessionCommandAlertReport,
    ReconcileSessionNotice,
)
from app.services.billing_quotes import conflict
from app.services.session_command_delivery import reconcile_session_command
from app.services.session_command_membership import utc_now
from app.services.wukongim_client import wukongim_client


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def alert_item(row: SessionCommandDelivery) -> SessionCommandAlertItem:
    return SessionCommandAlertItem(
        operation_id=row.id,
        project_id=row.project_id,
        kind=row.kind,
        notification_status=row.status,
        effects_status=row.effects_status,
        notification_attempts=row.attempts,
        effects_attempts=row.effects_attempts,
        notification_error=row.last_error,
        effects_error=row.effects_last_error,
        created_at=as_utc(row.created_at),
        notification_updated_at=as_utc(row.updated_at),
    )


def inspect_session_command_alerts(
    db: Session, *, stale_after_seconds: int = 300, offset: int = 0,
    limit: int = 50,
) -> SessionCommandAlertReport:
    """Return old unfinished delivery states for the platform operator."""
    checked_at = utc_now()
    cutoff = checked_at - timedelta(seconds=stale_after_seconds)
    overdue_notice = and_(
        SessionCommandDelivery.status.in_(("pending", "sending", "unknown")),
        SessionCommandDelivery.updated_at <= cutoff,
    )
    overdue_effects = and_(
        SessionCommandDelivery.effects_status.in_(("pending", "unknown")),
        SessionCommandDelivery.effects_updated_at <= cutoff,
    )
    query = db.query(SessionCommandDelivery).filter(
        or_(overdue_notice, overdue_effects)
    )
    rows = query.order_by(
        SessionCommandDelivery.created_at,
        SessionCommandDelivery.id,
    ).offset(offset).limit(limit).all()
    return SessionCommandAlertReport(
        checked_at=checked_at,
        stale_after_seconds=stale_after_seconds,
        offset=offset,
        total=query.count(),
        items=[alert_item(row) for row in rows],
    )


async def reconcile_stale_notice(
    db: Session, operator: PlatformOperator, operation_id: UUID,
    payload: ReconcileSessionNotice,
) -> SessionCommandAlertItem:
    """Check an old IM message receipt; never create or resend a message."""
    row = (
        db.query(SessionCommandDelivery)
        .filter(SessionCommandDelivery.id == operation_id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if row is None:
        exists = db.query(SessionCommandDelivery.id).filter(
            SessionCommandDelivery.id == operation_id,
        ).first()
        db.rollback()
        if exists is not None:
            raise conflict("操作正在处理，请稍后刷新")
        raise HTTPException(404, "会话操作不存在")
    updated_at = as_utc(row.updated_at)
    if (row.status != payload.expected_status
            or updated_at > utc_now() - timedelta(minutes=5)):
        db.rollback()
        raise conflict("操作状态已变化或尚未超时，请刷新后再核对")
    if not wukongim_client.enabled:
        db.rollback()
        raise HTTPException(503, "IM 服务当前不可用")

    audit = BillingAudit(
        operator_id=operator.id,
        project_id=row.project_id,
        action="session_notice_reconcile",
        reason=payload.reason,
        detail={
            "operation_id": str(row.id),
            "previous_status": row.status,
        },
    )
    db.add(audit)
    status = await reconcile_session_command(db, operation_id)
    audit.detail = {**audit.detail, "result_status": status}
    db.commit()
    current = db.get(SessionCommandDelivery, operation_id)
    assert current is not None
    return alert_item(current)
