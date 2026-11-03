"""Screen management never exposes a raw device execution endpoint."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.device_auth import require_management_project
from app.schemas.device_screen_publication import (
    ScreenLocalGrantRead,
    ScreenPolicyPublish,
    ScreenPolicyRead,
    ScreenPolicyRevoke,
)
from app.services.device_screen_publication import DeviceScreenPublicationService

router = APIRouter()


@router.get("/{device_id}/screen-grant", response_model=ScreenLocalGrantRead)
async def local_screen_grant(
    device_id: UUID,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ScreenLocalGrantRead:
    return await DeviceScreenPublicationService(db).local_grant(project_id, device_id)


@router.get("/{device_id}/screen-policy", response_model=ScreenPolicyRead)
async def get_screen_policy(
    device_id: UUID,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ScreenPolicyRead:
    policy = await DeviceScreenPublicationService(db).get(project_id, device_id)
    if policy is None:
        raise HTTPException(404, "Device screen policy not found")
    return policy


@router.put("/{device_id}/screen-policy", response_model=ScreenPolicyRead)
async def publish_screen_policy(
    device_id: UUID,
    request: ScreenPolicyPublish,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ScreenPolicyRead:
    return await DeviceScreenPublicationService(db).publish(
        project_id, device_id, request
    )


@router.post("/{device_id}/screen-policy/revoke", response_model=ScreenPolicyRead)
async def revoke_screen_policy(
    device_id: UUID,
    request: ScreenPolicyRevoke,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ScreenPolicyRead:
    return await DeviceScreenPublicationService(db).revoke(
        project_id, device_id, request
    )
