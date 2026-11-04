"""Private schedule configuration, behind the staff gateway's admin check."""

import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.workflow_schedule import (
    ScheduleDisable,
    ScheduleDisableInternal,
    ScheduleReceipt,
    ScheduleSave,
    ScheduleSaveInternal,
    ScheduleState,
)
from app.services import workflow_schedules
from app.services import workflow_schedule_views
from app.schemas.workflow_schedule_views import (
    SchedulePreviewRequest, SchedulePreview, ScheduleOccurrenceList,
)

router = APIRouter()


def require_internal_key(
    x_internal_api_key: str | None = Header(default=None),
) -> None:
    key = settings.SECRET_KEY
    if key is None or not key.get_secret_value():
        raise HTTPException(503, "定时计划内部身份核验未配置。")
    if not x_internal_api_key or not hmac.compare_digest(
        x_internal_api_key.encode(), key.get_secret_value().encode()
    ):
        raise HTTPException(403, "定时计划内部身份无法确认。")


@router.post(
    "/{workflow_id}/schedule/preview", response_model=SchedulePreview,
    dependencies=[Depends(require_internal_key)],
)
async def preview_schedule(
    workflow_id: str, request: SchedulePreviewRequest, response: Response,
    project_id: UUID = Query(...), db: AsyncSession = Depends(get_db),
) -> SchedulePreview:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_schedule_views.preview_schedule(
        db, workflow_id, project_id, request,
    )


@router.get(
    "/{workflow_id}/schedule/occurrences",
    response_model=ScheduleOccurrenceList,
    dependencies=[Depends(require_internal_key)],
)
async def list_occurrences(
    workflow_id: str, response: Response, project_id: UUID = Query(...),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0, le=2_147_483_647),
    db: AsyncSession = Depends(get_db),
) -> ScheduleOccurrenceList:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_schedule_views.list_occurrences(
        db, workflow_id, project_id, limit, offset,
    )


@router.get(
    "/{workflow_id}/schedule",
    response_model=ScheduleState,
    dependencies=[Depends(require_internal_key)],
)
async def get_schedule(
    workflow_id: str,
    response: Response,
    project_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> ScheduleState:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_schedules.get_schedule(db, workflow_id, project_id)


@router.put(
    "/{workflow_id}/schedule",
    response_model=ScheduleReceipt,
    dependencies=[Depends(require_internal_key)],
)
async def save_schedule(
    workflow_id: str,
    request: ScheduleSaveInternal,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> ScheduleReceipt:
    response.headers["Cache-Control"] = "no-store"
    public = ScheduleSave.model_validate(
        request.model_dump(include=set(ScheduleSave.model_fields))
    )
    return await workflow_schedules.save_schedule(
        db, workflow_id, request.project_id, request.actor_staff_id, public
    )


@router.post(
    "/{workflow_id}/schedule/disable",
    response_model=ScheduleReceipt,
    dependencies=[Depends(require_internal_key)],
)
async def disable_schedule(
    workflow_id: str,
    request: ScheduleDisableInternal,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> ScheduleReceipt:
    response.headers["Cache-Control"] = "no-store"
    public = ScheduleDisable.model_validate(
        request.model_dump(include=set(ScheduleDisable.model_fields))
    )
    return await workflow_schedules.disable_schedule(
        db, workflow_id, request.project_id, request.actor_staff_id, public
    )


@router.get(
    "/{workflow_id}/schedule/commands/{request_id}",
    response_model=ScheduleReceipt,
    dependencies=[Depends(require_internal_key)],
)
async def get_command_receipt(
    workflow_id: str,
    request_id: UUID,
    response: Response,
    project_id: UUID = Query(...),
    actor_staff_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> ScheduleReceipt:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_schedules.get_command_receipt(
        db, workflow_id, project_id, actor_staff_id, request_id
    )
