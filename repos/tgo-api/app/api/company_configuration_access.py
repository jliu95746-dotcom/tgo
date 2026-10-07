"""Company administrators own AI configuration; legacy rollout stays opt-in."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import resolve_staff_token
from app.models.company_account import CompanyAccount
from app.services.company_entitlements import require_new_service


def require_operator_model_management(db: Session = Depends(get_db)) -> None:
    """Tenant credentials cannot manage models after the shared catalogue is active."""
    from app.services.shared_models import shared_models_active

    if shared_models_active(db):
        raise HTTPException(403, "模型由平台统一配置，请联系平台管理员")


def require_configuration_write(
    request: Request, db: Session = Depends(get_db)
) -> None:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        return
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    actor = (
        resolve_staff_token(db, token) if scheme.lower() == "bearer" and token else None
    )
    if actor is None:
        raise HTTPException(401, "请先登录企业工作台")
    account = db.get(CompanyAccount, actor.project_id)
    if account is None:
        return
    if actor.role != "admin":
        raise HTTPException(403, "仅企业管理员可以修改企业 AI 配置")
    if request.method != "DELETE" and not request.url.path.endswith("/cancel"):
        require_new_service(db, actor.project_id)
