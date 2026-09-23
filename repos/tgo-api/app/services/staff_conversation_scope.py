"""One ownership rule for conversation details, history, files and realtime."""

from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import TGOAPIException
from app.core.security import resolve_staff_token
from app.models import Staff, Visitor, VisitorSession


def restricted_staff(user: Staff) -> bool:
    return settings.SAAS_BILLING_ENABLED and user.role != "admin"


def owned_visitor_ids(user: Staff) -> Select[tuple[UUID]]:
    latest_owner = (
        select(VisitorSession.staff_id)
        .where(
            VisitorSession.visitor_id == Visitor.id,
            VisitorSession.project_id == user.project_id,
        )
        .order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc())
        .limit(1)
        .correlate(Visitor)
        .scalar_subquery()
    )
    return select(Visitor.id).where(
        Visitor.project_id == user.project_id,
        Visitor.deleted_at.is_(None),
        latest_owner == user.id,
    )


def require_owned_visitor(db: Session, user: Staff, visitor_id: UUID) -> None:
    if (
        restricted_staff(user)
        and db.scalar(
            select(Visitor.id).where(
                Visitor.id == visitor_id,
                Visitor.id.in_(owned_visitor_ids(user)),
            )
        )
        is None
    ):
        raise TGOAPIException("会话不存在或不属于当前客服", "NOT_FOUND", status_code=404)


async def enforce_visitor_detail_scope(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> None:
    if not settings.SAAS_BILLING_ENABLED or credentials is None:
        return
    actor = resolve_staff_token(db, credentials.credentials)
    if actor is None or not restricted_staff(actor):
        return
    # Claiming is authorized by the queue/visitor lock, before history is visible.
    if request.method == "POST" and request.url.path.endswith("/accept"):
        return
    raw = request.path_params.get("visitor_id") or request.path_params.get(
        "channel_id"
    )
    if raw is None:
        return
    try:
        identifier = UUID(str(raw).removesuffix("-vtr"))
    except ValueError as exc:
        raise TGOAPIException("访客不存在", "NOT_FOUND", status_code=404) from exc
    require_owned_visitor(db, actor, identifier)
