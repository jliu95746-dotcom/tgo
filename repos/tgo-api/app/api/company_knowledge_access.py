"""Keep historical reads available while gating new knowledge work."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import resolve_staff_token
from app.services.company_entitlements import require_new_service


def require_knowledge_write(
    request: Request, db: Session = Depends(get_db),
) -> None:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        return
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    actor = resolve_staff_token(db, token) if scheme.lower() == "bearer" and token else None
    if actor is None:
        raise HTTPException(401, "请先登录企业工作台")
    if actor.role != "admin":
        raise HTTPException(403, "仅企业管理员可以配置知识库")
    if request.method != "DELETE":
        require_new_service(db, actor.project_id)
