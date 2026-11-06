"""Request a real, source-idempotent staff handoff from a reviewed task."""

from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.core.security import check_user_permission
from app.models import Platform, Project, Staff, Visitor, VisitorSession
from app.models.visitor_session import SessionStatus
from app.schemas.workflow_approval import WorkflowAfterSalesHandoffSummary
from app.services.aftersales_review_files import require_customer_read
from app.services.channel_access import require_staff_channel_access
from app.services.human_handoff_service import (
    _handoff_action_digest,
    _saved_handoff_receipt,
    request_human_handoff,
)
from app.services.workflow_client import workflow_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


HANDOFF_REASON = "售后待办需人工处理"
HANDOFF_ROUTING_REASON = "aftersales_review"


def require_customer_update(db: Session, staff: Staff) -> None:
    if not check_user_permission(db, staff, "visitors:update"):
        raise TGOAPIException("没有办理客户接待的权限", "FORBIDDEN", status_code=403)


async def _authorized_context(
    db: Session,
    staff: Staff,
    review_id: UUID,
) -> tuple[Project, VisitorSession, Visitor, Platform, str]:
    review = await workflow_client.get_aftersales_review(
        str(staff.project_id),
        str(staff.id),
        str(review_id),
    )
    if (
        review.status != "reviewed"
        or review.review_result != "needs_manual_service"
        or review.reviewed_by != staff.id
    ):
        raise HTTPException(409, "售后待办尚未由当前员工核对为需人工办理。")
    session = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.id == review.visitor_session_id,
            VisitorSession.project_id == staff.project_id,
        )
        .first()
    )
    if session is None or session.project_id != staff.project_id:
        raise HTTPException(409, "售后待办的客户会话已不可用。")
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.id == session.visitor_id,
            Visitor.project_id == staff.project_id,
            Visitor.platform_id == session.platform_id,
            Visitor.deleted_at.is_(None),
            Visitor.access_retired_at.is_(None),
        )
        .first()
    )
    if (
        visitor is None
        or visitor.id != session.visitor_id
        or visitor.project_id != staff.project_id
        or visitor.platform_id != session.platform_id
        or visitor.deleted_at is not None
        or visitor.access_retired_at is not None
    ):
        raise HTTPException(409, "售后待办的客户身份已不可用。")
    platform = (
        db.query(Platform)
        .filter(
            Platform.id == session.platform_id,
            Platform.project_id == staff.project_id,
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
        )
        .first()
    )
    if (
        platform is None
        or platform.id != session.platform_id
        or platform.project_id != staff.project_id
        or not platform.is_active
        or platform.deleted_at is not None
    ):
        raise HTTPException(409, "售后待办所属渠道已不可用。")
    project = (
        db.query(Project)
        .filter(
            Project.id == staff.project_id,
            Project.deleted_at.is_(None),
        )
        .first()
    )
    if (
        project is None
        or project.id != staff.project_id
        or project.deleted_at is not None
    ):
        raise HTTPException(409, "售后待办所属企业已不可用。")
    channel_id = build_visitor_channel_id(visitor.id)
    await require_staff_channel_access(
        db,
        staff,
        channel_id,
        CHANNEL_TYPE_CUSTOMER_SERVICE,
    )
    return project, session, visitor, platform, channel_id


def _receipt(
    db: Session,
    project: Project,
    visitor: Visitor,
    platform: Platform,
    channel_id: str,
    review_id: UUID,
) -> WorkflowAfterSalesHandoffSummary | None:
    source_id = f"aftersales-review:{review_id}"
    digest = _handoff_action_digest(
        HANDOFF_REASON,
        platform.type,
        channel_id,
        CHANNEL_TYPE_CUSTOMER_SERVICE,
    )
    saved = _saved_handoff_receipt(
        db, project.id, visitor.id, source_id, digest
    )
    if saved is None:
        return None
    try:
        return WorkflowAfterSalesHandoffSummary.model_validate(saved)
    except ValidationError:
        raise HTTPException(409, "人工接待回执不完整，请人工核查。") from None


async def get_aftersales_handoff(
    db: Session,
    staff: Staff,
    review_id: UUID,
) -> WorkflowAfterSalesHandoffSummary | None:
    require_customer_read(db, staff)
    project, _, visitor, platform, channel_id = await _authorized_context(
        db,
        staff,
        review_id,
    )
    return _receipt(db, project, visitor, platform, channel_id, review_id)


async def request_aftersales_handoff(
    db: Session,
    staff: Staff,
    review_id: UUID,
) -> WorkflowAfterSalesHandoffSummary:
    require_customer_update(db, staff)
    (
        project,
        session,
        visitor,
        platform,
        channel_id,
    ) = await _authorized_context(
        db,
        staff,
        review_id,
    )
    existing = _receipt(db, project, visitor, platform, channel_id, review_id)
    if existing is not None:
        return existing
    if (
        session.status != SessionStatus.OPEN.value
        or session.closed_at is not None
    ):
        raise HTTPException(409, "原客户会话已结束，不能再次发起人工接待。")
    other_open_session = (
        db.query(VisitorSession.id)
        .filter(
            VisitorSession.project_id == staff.project_id,
            VisitorSession.visitor_id == visitor.id,
            VisitorSession.id != session.id,
            VisitorSession.status == SessionStatus.OPEN.value,
            VisitorSession.closed_at.is_(None),
        )
        .first()
    )
    if other_open_session is not None:
        raise HTTPException(409, "客户已有另一条进行中的会话，请先人工核查。")
    result = await request_human_handoff(
        db=db,
        project=project,
        visitor=visitor,
        reason=HANDOFF_REASON,
        source_message_id=f"aftersales-review:{review_id}",
        routing_reason=HANDOFF_ROUTING_REASON,
        channel=platform.type,
        channel_id=channel_id,
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        strict_source_receipt=True,
    )
    try:
        return WorkflowAfterSalesHandoffSummary.model_validate(result)
    except ValidationError:
        raise HTTPException(409, "人工接待回执不完整，请人工核查。") from None
