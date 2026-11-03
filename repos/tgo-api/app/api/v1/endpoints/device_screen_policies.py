"""Admin-only screen policy and explicit mode withdrawal gateway."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.v1.endpoints.device_control import _handle_service_error
from app.core.security import require_admin
from app.models import Staff
from app.schemas.device_read_policy import (
    DeviceReadPolicyRead,
    DeviceReadPolicyRevoke,
)
from app.schemas.device_screen_publication import (
    ScreenLocalGrantRead,
    ScreenPolicyPublish,
    ScreenPolicyRead,
    ScreenPolicyRevoke,
)
from app.services.device_control_client import device_control_client

router = APIRouter()


@router.get(
    "/devices/{device_id}/screen-grant", response_model=ScreenLocalGrantRead
)
async def get_screen_grant(
    device_id: UUID, current_user: Staff = Depends(require_admin())
) -> ScreenLocalGrantRead:
    try:
        return await device_control_client.get_screen_grant(
            device_id, str(current_user.project_id)
        )
    except Exception as error:
        _handle_service_error(error, "get_screen_grant")


@router.get(
    "/devices/{device_id}/screen-policy", response_model=ScreenPolicyRead
)
async def get_screen_policy(
    device_id: UUID, current_user: Staff = Depends(require_admin())
) -> ScreenPolicyRead:
    try:
        return await device_control_client.get_screen_policy(
            device_id, str(current_user.project_id)
        )
    except Exception as error:
        _handle_service_error(error, "get_screen_policy")


@router.put(
    "/devices/{device_id}/screen-policy", response_model=ScreenPolicyRead
)
async def publish_screen_policy(
    device_id: UUID,
    request: ScreenPolicyPublish,
    current_user: Staff = Depends(require_admin()),
) -> ScreenPolicyRead:
    try:
        return await device_control_client.publish_screen_policy(
            device_id, str(current_user.project_id), request
        )
    except Exception as error:
        _handle_service_error(error, "publish_screen_policy")


@router.post(
    "/devices/{device_id}/screen-policy/revoke",
    response_model=ScreenPolicyRead,
)
async def revoke_screen_policy(
    device_id: UUID,
    request: ScreenPolicyRevoke,
    current_user: Staff = Depends(require_admin()),
) -> ScreenPolicyRead:
    try:
        return await device_control_client.revoke_screen_policy(
            device_id, str(current_user.project_id), request
        )
    except Exception as error:
        _handle_service_error(error, "revoke_screen_policy")


@router.post(
    "/devices/{device_id}/read-policy/revoke",
    response_model=DeviceReadPolicyRead,
)
async def revoke_read_policy(
    device_id: UUID,
    request: DeviceReadPolicyRevoke,
    current_user: Staff = Depends(require_admin()),
) -> DeviceReadPolicyRead:
    try:
        return await device_control_client.revoke_read_policy(
            device_id, str(current_user.project_id), request
        )
    except Exception as error:
        _handle_service_error(error, "revoke_read_policy")
