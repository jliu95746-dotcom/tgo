"""Project-scoped monitoring reads and execution-scoped lifecycle writes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.device_auth import require_management_project
from app.core.device_session_auth import require_session_writer
from app.schemas.device_access import DeviceServicePrincipal
from app.schemas.device_session import (
    SessionDetail,
    SessionFinish,
    SessionList,
    SessionStart,
    SessionSummary,
)
from app.services.device_sessions import DeviceSessionService

router = APIRouter()


@router.get("", response_model=SessionList)
async def list_sessions(
    project: UUID = Depends(require_management_project),
    device_id: UUID | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
) -> SessionList:
    return await DeviceSessionService(db).list(
        project, device_id=device_id, skip=skip, limit=limit
    )


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: UUID,
    project: UUID = Depends(require_management_project),
    step_skip: int = Query(0, ge=0),
    step_limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
) -> SessionDetail:
    return await DeviceSessionService(db).get(
        project, session_id, step_skip=step_skip, step_limit=step_limit
    )


@router.post("/{device_id}/{session_id}/start", response_model=SessionSummary)
async def start_session(
    device_id: UUID,
    session_id: UUID,
    request: SessionStart,
    principal: DeviceServicePrincipal = Depends(require_session_writer),
    db: AsyncSession = Depends(get_async_db),
) -> SessionSummary:
    return await DeviceSessionService(db).start(
        principal.project_id, device_id, session_id, request
    )


@router.post(
    "/{device_id}/{session_id}/heartbeat", response_model=SessionSummary
)
async def heartbeat_session(
    device_id: UUID,
    session_id: UUID,
    principal: DeviceServicePrincipal = Depends(require_session_writer),
    db: AsyncSession = Depends(get_async_db),
) -> SessionSummary:
    return await DeviceSessionService(db).heartbeat(
        principal.project_id, device_id, session_id
    )


@router.post("/{device_id}/{session_id}/finish", response_model=SessionSummary)
async def finish_session(
    device_id: UUID,
    session_id: UUID,
    request: SessionFinish,
    principal: DeviceServicePrincipal = Depends(require_session_writer),
    db: AsyncSession = Depends(get_async_db),
) -> SessionSummary:
    return await DeviceSessionService(db).finish(
        principal.project_id, device_id, session_id, request.status
    )
