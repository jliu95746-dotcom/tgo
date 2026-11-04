"""Admin-only reviewed script metadata; no executable RPC proxy."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.security import require_admin
from app.models import Staff
from app.schemas.device_script_policy import ScriptCatalog
from app.schemas.device_script_publication import (
    ScriptLocalStatusRead,
    ScriptPolicyPublish,
    ScriptPolicyRead,
    ScriptPolicyRevoke,
)
from app.services.device_control_client import device_control_client

router = APIRouter()


@router.get(
    "/devices/{device_id}/script-catalog", response_model=ScriptCatalog
)
async def get_script_catalog(
    device_id: UUID, current_user: Staff = Depends(require_admin())
) -> ScriptCatalog:
    return await device_control_client.get_script_catalog(
        device_id, str(current_user.project_id)
    )


@router.get(
    "/devices/{device_id}/script-status", response_model=ScriptLocalStatusRead
)
async def get_script_status(
    device_id: UUID, current_user: Staff = Depends(require_admin())
) -> ScriptLocalStatusRead:
    return await device_control_client.get_script_status(
        device_id, str(current_user.project_id)
    )


@router.get(
    "/devices/{device_id}/script-policy", response_model=ScriptPolicyRead
)
async def get_script_policy(
    device_id: UUID, current_user: Staff = Depends(require_admin())
) -> ScriptPolicyRead:
    return await device_control_client.get_script_policy(
        device_id, str(current_user.project_id)
    )


@router.put(
    "/devices/{device_id}/script-policy", response_model=ScriptPolicyRead
)
async def publish_script_policy(
    device_id: UUID,
    request: ScriptPolicyPublish,
    current_user: Staff = Depends(require_admin()),
) -> ScriptPolicyRead:
    return await device_control_client.publish_script_policy(
        device_id, str(current_user.project_id), request
    )


@router.post(
    "/devices/{device_id}/script-policy/revoke",
    response_model=ScriptPolicyRead,
)
async def revoke_script_policy(
    device_id: UUID,
    request: ScriptPolicyRevoke,
    current_user: Staff = Depends(require_admin()),
) -> ScriptPolicyRead:
    return await device_control_client.revoke_script_policy(
        device_id, str(current_user.project_id), request
    )
