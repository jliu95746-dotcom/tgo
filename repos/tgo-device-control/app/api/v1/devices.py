"""Device management API endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.device_auth import require_management_project
from app.schemas.device import (
    BindCodeResponse,
    DeviceResponse,
    DeviceListResponse,
    DeviceUpdateRequest,
    DeviceStatus,
    DeviceType,
    ConnectedDeviceListResponse,
)
from app.schemas.device_scenario import (
    DeviceScenario,
    DeviceScenarioDraftRead,
    DeviceScenarioDraftWrite,
)
from app.services.device_service import DeviceService
from app.services.device_scenario import DeviceScenarioService
from app.schemas.device_read_policy import (
    DeviceReadPolicyPublish,
    DeviceReadPolicyRead,
    DeviceReadPolicyRevoke,
    DeviceReadPolicyTest,
)
from app.services.device_read_policy import DeviceReadPolicyService

router = APIRouter()


@router.post(
    "/{device_id}/read-policy/revoke", response_model=DeviceReadPolicyRead
)
async def revoke_device_read_policy(
    device_id: UUID,
    request: DeviceReadPolicyRevoke,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> DeviceReadPolicyRead:
    return await DeviceReadPolicyService(db).revoke(
        project_id, device_id, request
    )


@router.get("/{device_id}/read-policy", response_model=DeviceReadPolicyRead)
async def get_device_read_policy(
    device_id: UUID,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> DeviceReadPolicyRead:
    policy = await DeviceReadPolicyService(db).get(project_id, device_id)
    if policy is None:
        raise HTTPException(404, "Device read policy not found")
    return policy


@router.put("/{device_id}/read-policy", response_model=DeviceReadPolicyRead)
async def publish_device_read_policy(
    device_id: UUID,
    request: DeviceReadPolicyPublish,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> DeviceReadPolicyRead:
    return await DeviceReadPolicyService(db).publish(
        project_id, device_id, request
    )


@router.post(
    "/{device_id}/read-policy/test", response_model=DeviceReadPolicyRead
)
async def test_device_read_policy(
    device_id: UUID,
    request: DeviceReadPolicyTest,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> DeviceReadPolicyRead:
    return await DeviceReadPolicyService(db).test_read(
        project_id, device_id, request
    )


@router.get(
    "/{device_id}/scenario-drafts/{scenario}",
    response_model=DeviceScenarioDraftRead,
)
async def get_device_scenario_draft(
    device_id: UUID,
    scenario: DeviceScenario,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> DeviceScenarioDraftRead:
    draft = await DeviceScenarioService(db).get(
        project_id, device_id, scenario
    )
    if draft is None:
        raise HTTPException(
            status_code=404, detail="Device scenario draft not found"
        )
    return draft


@router.put(
    "/{device_id}/scenario-drafts/{scenario}",
    response_model=DeviceScenarioDraftRead,
)
async def save_device_scenario_draft(
    device_id: UUID,
    scenario: DeviceScenario,
    request: DeviceScenarioDraftWrite,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> DeviceScenarioDraftRead:
    return await DeviceScenarioService(db).save(
        project_id, device_id, scenario, request
    )


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    project_id: UUID = Depends(require_management_project),
    device_type: Optional[DeviceType] = Query(
        None, description="Filter by device type"
    ),
    status: Optional[DeviceStatus] = Query(
        None, description="Filter by status"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """List all devices for a project."""
    service = DeviceService(db)
    devices, total = await service.list_devices(
        project_id=project_id,
        device_type=device_type,
        status=status,
        skip=skip,
        limit=limit,
    )
    return DeviceListResponse(devices=devices, total=total)


@router.get("/connected", response_model=ConnectedDeviceListResponse)
async def list_connected_devices(
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ConnectedDeviceListResponse:
    (
        "Return only live, still-owned "
        "connections without a global device "
        "listing."
    )
    return await DeviceService(db).list_connected_devices(project_id)


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: UUID,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a specific device by ID."""
    service = DeviceService(db)
    device = await service.get_device(
        device_id=device_id, project_id=project_id
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.post("/bind-code", response_model=BindCodeResponse)
async def generate_bind_code(
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
):
    """Generate a new bind code for device registration."""
    service = DeviceService(db)
    bind_code, expires_at = await service.generate_bind_code(
        project_id=project_id
    )
    return BindCodeResponse(bind_code=bind_code, expires_at=expires_at)


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: UUID,
    request: DeviceUpdateRequest,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a device."""
    service = DeviceService(db)
    device = await service.update_device(
        device_id=device_id,
        project_id=project_id,
        update_data=request,
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.delete("/{device_id}")
async def delete_device(
    device_id: UUID,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete (unbind) a device."""
    service = DeviceService(db)
    success = await service.delete_device(
        device_id=device_id, project_id=project_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"success": True, "message": "Device deleted successfully"}


@router.post("/{device_id}/disconnect")
async def disconnect_device(
    device_id: UUID,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
):
    """Force disconnect a device."""
    service = DeviceService(db)
    success = await service.disconnect_device(
        device_id=device_id, project_id=project_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"success": True, "message": "Device disconnected"}
