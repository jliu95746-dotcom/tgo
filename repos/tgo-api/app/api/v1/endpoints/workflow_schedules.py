"""Administrative schedules with server-selected actor and tenant."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.workflow_schedule import (
    ScheduleDisable,
    ScheduleReceipt,
    ScheduleSave,
    ScheduleState,
)
from app.services.workflow_client import workflow_client
from app.schemas.workflow_schedule_views import (
    SchedulePreviewRequest, SchedulePreview, ScheduleOccurrenceList,
)
from app.services.workflow_schedule_authority import (
    ScheduleActor,
    require_current_schedule_admin,
)

router = APIRouter()


@router.post("/{workflow_id}/schedule/preview", response_model=SchedulePreview)
async def preview_schedule(
    workflow_id: str, request: SchedulePreviewRequest, response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> SchedulePreview:
    actor = ScheduleActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.preview_schedule(
        workflow_id, actor.project_id, request,
    )
    require_current_schedule_admin(db, actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "/{workflow_id}/schedule/occurrences",
    response_model=ScheduleOccurrenceList,
)
async def list_schedule_occurrences(
    workflow_id: str, response: Response,
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0, le=2_147_483_647),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ScheduleOccurrenceList:
    actor = ScheduleActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.list_schedule_occurrences(
        workflow_id, actor.project_id, limit, offset,
    )
    require_current_schedule_admin(db, actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{workflow_id}/schedule", response_model=ScheduleState)
async def get_schedule(
    workflow_id: str,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ScheduleState:
    actor = ScheduleActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.get_schedule(workflow_id, actor.project_id)
    require_current_schedule_admin(db, actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.put("/{workflow_id}/schedule", response_model=ScheduleReceipt)
async def save_schedule(
    workflow_id: str,
    request: ScheduleSave,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ScheduleReceipt:
    actor = ScheduleActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.save_schedule(
        workflow_id, actor.project_id, actor.staff_id, request
    )
    require_current_schedule_admin(db, actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{workflow_id}/schedule/disable", response_model=ScheduleReceipt)
async def disable_schedule(
    workflow_id: str,
    request: ScheduleDisable,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ScheduleReceipt:
    actor = ScheduleActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.disable_schedule(
        workflow_id, actor.project_id, actor.staff_id, request
    )
    require_current_schedule_admin(db, actor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "/{workflow_id}/schedule/commands/{request_id}",
    response_model=ScheduleReceipt,
)
async def get_command_receipt(
    workflow_id: str,
    request_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ScheduleReceipt:
    actor = ScheduleActor.from_staff(current_user)
    require_current_schedule_admin(db, actor)
    result = await workflow_client.get_schedule_command(
        workflow_id, actor.project_id, actor.staff_id, request_id
    )
    require_current_schedule_admin(db, actor)
    response.headers["Cache-Control"] = "no-store"
    return result
