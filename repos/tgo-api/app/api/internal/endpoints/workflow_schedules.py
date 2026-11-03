"""Current timer actor permission on the private service listener only."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.api.internal.endpoints.workflow_approvals import (
    require_workflow_service,
)
from app.core.database import get_db
from app.schemas.timer_authority import TimerAuthority
from app.services.company_entitlements import new_service_available
from app.services.workflow_schedule_authority import schedule_admin_query

router = APIRouter(dependencies=[Depends(require_workflow_service)])


@router.post("/authority/verify", response_model=TimerAuthority)
def verify_timer_authority(
    query: TimerAuthority, response: Response, db: Session = Depends(get_db)
) -> TimerAuthority:
    allowed = (
        db.scalar(schedule_admin_query(query.project_id, query.actor_staff_id))
        is not None
    )
    if not allowed or not new_service_available(db, query.project_id):
        raise HTTPException(409, "定时计划授权人或当前服务权限已失效。")
    response.headers["Cache-Control"] = "no-store"
    return query
