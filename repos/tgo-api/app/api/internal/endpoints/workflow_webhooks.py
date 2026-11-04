"""Current administrator and company checks on the private listener only."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.api.internal.endpoints.workflow_approvals import (
    require_workflow_service,
)
from app.core.config import settings
from app.core.database import get_db
from app.schemas.webhook_authority import WebhookAuthority
from app.services.company_entitlements import new_service_available
from app.services.workflow_schedule_authority import schedule_admin_query

router = APIRouter(dependencies=[Depends(require_workflow_service)])


@router.post("/authority/verify", response_model=WebhookAuthority)
def verify_authority(
    query: WebhookAuthority, response: Response, db: Session = Depends(get_db)
) -> WebhookAuthority:
    if (
        not settings.WEBHOOK_EXECUTION_ENABLED
        or db.scalar(
            schedule_admin_query(query.project_id, query.actor_staff_id)
        )
        is None
        or not new_service_available(db, query.project_id)
    ):
        raise HTTPException(409, "外部通知授权人、服务权限或接收开关已失效。")
    response.headers["Cache-Control"] = "no-store"
    return query
