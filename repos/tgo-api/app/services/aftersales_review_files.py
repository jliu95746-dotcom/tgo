"""Find chat uploads belonging to one trusted aftersales visitor session."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.core.security import check_user_permission
from app.models import (
    ChatFile,
    Platform,
    Project,
    Staff,
    Visitor,
    VisitorSession,
)
from app.schemas.ai_workflows import WorkflowExecution
from app.schemas.workflow_approval import (
    WorkflowAfterSalesChatTarget,
    WorkflowAfterSalesEvidenceFile,
)
from app.services.channel_access import require_staff_channel_access
from app.services.workflow_client import workflow_client
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


def require_customer_read(db: Session, staff: Staff) -> None:
    if not check_user_permission(db, staff, "visitors:read"):
        raise TGOAPIException("没有查看客户资料的权限", "FORBIDDEN", status_code=403)


async def review_chat_target(
    db: Session,
    staff: Staff,
    visitor_session_id: UUID,
    *,
    source_message_id: str | None = None,
    source_channel: str | None = None,
    execution_id: UUID | None = None,
) -> WorkflowAfterSalesChatTarget:
    """Resolve a task's original visitor through current tenant and chat access."""
    require_customer_read(db, staff)
    context = _review_session_context(db, staff, visitor_session_id)
    if context is None:
        raise TGOAPIException(
            "售后待办的客户会话已不可用。",
            "NOT_FOUND",
            status_code=404,
        )
    session, visitor_id = context
    channel_id = build_visitor_channel_id(visitor_id)
    await require_staff_channel_access(
        db,
        staff,
        channel_id,
        CHANNEL_TYPE_CUSTOMER_SERVICE,
    )
    if execution_id is not None:
        try:
            execution = WorkflowExecution.model_validate(
                await workflow_client.get_execution(
                    str(execution_id),
                    str(staff.project_id),
                )
            )
            if (
                execution.id == str(execution_id)
                and execution.visitor_session_id == str(visitor_session_id)
                and execution.execution_mode == "production"
            ):
                source_message_id = execution.source_message_id
                source_channel = execution.source_channel
        except (HTTPException, ValidationError):
            pass
    return WorkflowAfterSalesChatTarget(
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        channel_id=channel_id,
        message_seq=await _verified_source_message_seq(
            db,
            session,
            visitor_id,
            channel_id,
            source_message_id,
            source_channel,
        ),
    )


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )


async def _verified_source_message_seq(
    db: Session,
    session: VisitorSession,
    visitor_id: UUID,
    channel_id: str,
    source_message_id: str | None,
    source_channel: str | None,
) -> int | None:
    """Locate only the persisted customer message from this exact session."""
    if (
        source_channel not in {"web", "app", "wecom_kf"}
        or not isinstance(source_message_id, str)
        or not source_message_id
        or len(source_message_id) > 255
        or not wukongim_client.enabled
    ):
        return None
    correlation = f"{channel_id}:{channel_id}:{source_message_id}"
    client_no = (
        "platform_"
        + hashlib.sha256(correlation.encode("utf-8")).hexdigest()[:32]
    )
    candidates = [client_no]
    if len(source_message_id) <= 100 and source_message_id != client_no:
        candidates.append(source_message_id)
    for candidate in candidates:
        try:
            message = await wukongim_client.get_message_by_client_msg_no(
                channel_id=channel_id,
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                client_msg_no=candidate,
                raise_on_error=True,
            )
        except Exception:
            return None
        if message is None:
            continue
        payload = message.payload
        extra = payload.get("extra") if isinstance(payload, dict) else None
        source_in_payload = (
            extra.get("message_id") if isinstance(extra, dict) else None
        )
        if (
            message.channel_id != channel_id
            or message.channel_type != CHANNEL_TYPE_CUSTOMER_SERVICE
            or message.from_uid != channel_id
            or message.client_msg_no != candidate
            or type(message.message_id) is not int
            or message.message_id <= 0
            or type(message.message_seq) is not int
            or message.message_seq <= 0
            or type(message.timestamp) is not int
            or message.timestamp <= 0
            or not isinstance(payload, dict)
            or type(payload.get("type")) is not int
            or payload.get("type") not in {1, 2, 3, 4}
            or (
                source_in_payload != source_message_id
                and message.client_msg_no != source_message_id
            )
        ):
            continue
        try:
            message_time = datetime.fromtimestamp(message.timestamp, UTC)
        except (OverflowError, OSError, ValueError):
            continue
        if message.timestamp < int(_utc(session.created_at).timestamp()):
            continue
        if session.closed_at is not None and message.timestamp > int(
            _utc(session.closed_at).timestamp()
        ):
            continue
        overlap = db.scalar(
            select(VisitorSession.id).where(
                VisitorSession.project_id == session.project_id,
                VisitorSession.visitor_id == visitor_id,
                VisitorSession.id != session.id,
                VisitorSession.created_at
                < message_time + timedelta(seconds=1),
                or_(
                    VisitorSession.closed_at.is_(None),
                    VisitorSession.closed_at >= message_time,
                ),
            )
        )
        if overlap is None:
            return message.message_seq
    return None


async def list_review_files(
    db: Session,
    staff: Staff,
    visitor_session_id: UUID,
) -> list[WorkflowAfterSalesEvidenceFile]:
    """List metadata only; downloading still uses the normal file access guard."""
    require_customer_read(db, staff)
    context = _review_session_context(db, staff, visitor_session_id)
    if context is None:
        return []
    session, visitor_id = context
    channel_id = build_visitor_channel_id(visitor_id)
    await require_staff_channel_access(
        db,
        staff,
        channel_id,
        CHANNEL_TYPE_CUSTOMER_SERVICE,
    )
    query = select(ChatFile).where(
        ChatFile.project_id == staff.project_id,
        ChatFile.channel_id == channel_id,
        ChatFile.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE,
        ChatFile.deleted_at.is_(None),
        ChatFile.created_at >= session.created_at,
    )
    if session.closed_at is not None:
        query = query.where(ChatFile.created_at <= session.closed_at)
    overlapping_session = (
        select(VisitorSession.id)
        .where(
            VisitorSession.project_id == staff.project_id,
            VisitorSession.visitor_id == visitor_id,
            VisitorSession.id != session.id,
            VisitorSession.created_at <= ChatFile.created_at,
            or_(
                VisitorSession.closed_at.is_(None),
                VisitorSession.closed_at >= ChatFile.created_at,
            ),
        )
        .exists()
    )
    query = query.where(~overlapping_session)
    rows = db.scalars(
        query.order_by(
            ChatFile.created_at.desc(),
            ChatFile.id.desc(),
        ).limit(100)
    ).all()
    return [WorkflowAfterSalesEvidenceFile.model_validate(row) for row in rows]


def _review_session_context(
    db: Session,
    staff: Staff,
    visitor_session_id: UUID,
) -> tuple[VisitorSession, UUID] | None:
    """Resolve the current customer session without trusting the review ID alone."""
    session = db.scalar(
        select(VisitorSession)
        .where(
            VisitorSession.id == visitor_session_id,
            VisitorSession.project_id == staff.project_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        session is None
        or session.platform_id is None
        or (session.status != "open" and session.closed_at is None)
    ):
        return None
    visitor_id = db.scalar(
        select(Visitor.id)
        .join(Platform, Platform.id == Visitor.platform_id)
        .join(Project, Project.id == Visitor.project_id)
        .where(
            Visitor.id == session.visitor_id,
            Visitor.project_id == staff.project_id,
            Visitor.platform_id == session.platform_id,
            Visitor.deleted_at.is_(None),
            Visitor.access_retired_at.is_(None),
            Platform.project_id == staff.project_id,
            Platform.deleted_at.is_(None),
            Platform.is_active.is_(True),
            Project.deleted_at.is_(None),
        )
    )
    if visitor_id is None:
        return None
    return session, visitor_id


async def require_intake_evidence_files(
    db: Session,
    staff: Staff,
    visitor_session_id: UUID,
    selected_ids: list[UUID],
) -> None:
    """Recheck selected files at submission time using the trusted session."""
    require_customer_read(db, staff)
    context = _review_session_context(db, staff, visitor_session_id)
    if context is None:
        raise TGOAPIException(
            "售后待办的客户会话已不可用，请刷新后重新核对。",
            "CONFLICT",
            status_code=409,
        )
    _, visitor_id = context
    await require_staff_channel_access(
        db,
        staff,
        build_visitor_channel_id(visitor_id),
        CHANNEL_TYPE_CUSTOMER_SERVICE,
    )
    if not selected_ids:
        return
    eligible = await list_review_files(db, staff, visitor_session_id)
    available_ids = {file.id for file in eligible}
    if not set(selected_ids).issubset(available_ids):
        raise TGOAPIException(
            "售后附件不属于当前会话或已不可用，请刷新后重新选择。",
            "CONFLICT",
            status_code=409,
        )
