"""Signed management routes publish scope without exposing script execution."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.device_auth import require_management_project
from app.schemas.device_script_budget import ScriptBudgetRead
from app.services.device_script_budget import DeviceScriptBudgetService
from app.schemas.device_script_publication import (
    ScriptLocalStatusRead,
    ScriptPolicyPublish,
    ScriptPolicyRead,
    ScriptPolicyRevoke,
)
from app.services.device_script_publication import (
    DeviceScriptPublicationService,
)

router = APIRouter()


@router.get("/{device_id}/script-budget", response_model=ScriptBudgetRead)
async def get_script_budget(
    device_id: UUID,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ScriptBudgetRead:
    return await DeviceScriptBudgetService(db).get(project_id, device_id)


@router.get("/{device_id}/script-status", response_model=ScriptLocalStatusRead)
async def local_status(
    device_id: UUID,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ScriptLocalStatusRead:
    return await DeviceScriptPublicationService(db).local_status(
        project_id, device_id
    )


@router.get("/{device_id}/script-policy", response_model=ScriptPolicyRead)
async def get_policy(
    device_id: UUID,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ScriptPolicyRead:
    result = await DeviceScriptPublicationService(db).get(
        project_id, device_id
    )
    if result is None:
        raise HTTPException(404, "Device script policy not found")
    return result


@router.put("/{device_id}/script-policy", response_model=ScriptPolicyRead)
async def publish_policy(
    device_id: UUID,
    request: ScriptPolicyPublish,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ScriptPolicyRead:
    return await DeviceScriptPublicationService(db).publish(
        project_id, device_id, request
    )


@router.post(
    "/{device_id}/script-policy/revoke", response_model=ScriptPolicyRead
)
async def revoke_policy(
    device_id: UUID,
    request: ScriptPolicyRevoke,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ScriptPolicyRead:
    return await DeviceScriptPublicationService(db).revoke(
        project_id, device_id, request
    )
