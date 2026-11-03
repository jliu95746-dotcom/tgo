"""Administrative webhook configuration with current actor checks."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.workflow_webhook import (
    WebhookChange,
    WebhookReceipt,
    WebhookSave,
    WebhookState,
)
from app.services.workflow_client import workflow_client
from app.schemas.webhook_history import WebhookDeliveryList
from app.services.workflow_schedule_authority import (
    ScheduleActor as WebhookActor,
    require_current_schedule_admin,
)

router = APIRouter()


@router.get(
    "/{workflow_id}/webhook/deliveries",
    response_model=WebhookDeliveryList,
)
async def get_deliveries(
    workflow_id: str,
    response: Response,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WebhookDeliveryList:
    actor = WebhookActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.get_webhook_deliveries(
        workflow_id,
        actor.project_id,
        limit,
        offset,
    )
    require_current_schedule_admin(db, actor)
    if (
        result.workflow_id != workflow_id
        or result.project_id != actor.project_id
    ):
        raise HTTPException(502, "外部投递记录与当前企业或流程不一致。")
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{workflow_id}/webhook", response_model=WebhookState)
async def get_webhook(
    workflow_id: str,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WebhookState:
    actor = WebhookActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.get_webhook(workflow_id, actor.project_id)
    require_current_schedule_admin(db, actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.put("/{workflow_id}/webhook", response_model=WebhookReceipt)
async def save_webhook(
    workflow_id: str,
    request: WebhookSave,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WebhookReceipt:
    actor = WebhookActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.save_webhook(
        workflow_id,
        actor.project_id,
        actor.staff_id,
        request,
    )
    require_current_schedule_admin(db, actor)
    response.headers["Cache-Control"] = "no-store"
    return result


async def _change(
    workflow_id: str,
    request: WebhookChange,
    response: Response,
    current_user: Staff,
    db: Session,
    operation: Literal["disable", "rotate"],
) -> WebhookReceipt:
    actor = WebhookActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.change_webhook(
        workflow_id,
        actor.project_id,
        actor.staff_id,
        operation,
        request,
    )
    require_current_schedule_admin(db, actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{workflow_id}/webhook/disable", response_model=WebhookReceipt)
async def disable_webhook(
    workflow_id: str,
    request: WebhookChange,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WebhookReceipt:
    return await _change(
        workflow_id, request, response, current_user, db, "disable"
    )


@router.post("/{workflow_id}/webhook/rotate", response_model=WebhookReceipt)
async def rotate_webhook(
    workflow_id: str,
    request: WebhookChange,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WebhookReceipt:
    return await _change(
        workflow_id, request, response, current_user, db, "rotate"
    )


@router.get(
    "/{workflow_id}/webhook/commands/{request_id}",
    response_model=WebhookReceipt,
)
async def get_command_receipt(
    workflow_id: str,
    request_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WebhookReceipt:
    actor = WebhookActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.get_webhook_command(
        workflow_id,
        actor.project_id,
        actor.staff_id,
        request_id,
    )
    require_current_schedule_admin(db, actor)
    response.headers["Cache-Control"] = "no-store"
    return result
