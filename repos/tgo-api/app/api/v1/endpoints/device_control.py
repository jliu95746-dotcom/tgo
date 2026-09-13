"""Device Control API endpoints - Proxies to tgo-device-control service."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import httpx

from app.core.logging import get_logger
from app.core.security import get_current_active_user
from app.services.device_control_client import device_control_client
from app.models import Staff
from app.core.exceptions import TGOAPIException
from app.schemas.device_debug_chat import DeviceDebugChatRequest
from app.schemas.device_session import DeviceSessionDetail, DeviceSessionList
from app.services.device_debug_chat import (
    stream_device_debug,
    validate_device_debug,
)

logger = get_logger("api.device_control")
router = APIRouter()


@router.get("/sessions", response_model=DeviceSessionList)
async def list_device_sessions(
    device_id: UUID | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Staff = Depends(get_current_active_user),
) -> DeviceSessionList:
    return await device_control_client.list_sessions(
        str(current_user.project_id),
        device_id=device_id,
        skip=skip,
        limit=limit,
    )


@router.get("/sessions/{session_id}", response_model=DeviceSessionDetail)
async def get_device_session(
    session_id: UUID,
    step_skip: int = Query(0, ge=0),
    step_limit: int = Query(100, ge=1, le=100),
    current_user: Staff = Depends(get_current_active_user),
) -> DeviceSessionDetail:
    return await device_control_client.get_session(
        session_id,
        str(current_user.project_id),
        step_skip=step_skip,
        step_limit=step_limit,
    )


def _handle_service_error(e: Exception, context: str):
    """Helper to handle errors from device control service."""
    if isinstance(e, (HTTPException, TGOAPIException)):
        raise e

    if isinstance(e, httpx.HTTPStatusError):
        if 400 <= e.response.status_code < 500:
            try:
                detail = e.response.json().get("detail", e.response.text)
            except Exception:
                detail = e.response.text
            raise HTTPException(
                status_code=e.response.status_code, detail=detail
            )
        logger.error(
            f"Device control error ({e.response.status_code}) during {context}"
        )
        raise HTTPException(
            status_code=502, detail="Device control service error"
        )

    if isinstance(e, httpx.RequestError):
        logger.error(
            f"Device control service connection error during {context}: {e}"
        )
        raise HTTPException(
            status_code=502, detail="Device control service unavailable"
        )

    logger.error(f"Unexpected error during {context}: {e}")
    raise HTTPException(
        status_code=500, detail=f"Internal server error: {str(e)}"
    )


@router.get("/devices")
async def list_devices(
    device_type: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: Staff = Depends(get_current_active_user),
):
    """List all devices for the current project."""
    try:
        return await device_control_client.list_devices(
            project_id=str(current_user.project_id),
            device_type=device_type,
            status=status,
            skip=skip,
            limit=limit,
        )
    except Exception as e:
        _handle_service_error(e, "list_devices")


@router.get("/devices/{device_id}")
async def get_device(
    device_id: str,
    current_user: Staff = Depends(get_current_active_user),
):
    """Get a specific device by ID."""
    try:
        result = await device_control_client.get_device(
            device_id=device_id,
            project_id=str(current_user.project_id),
        )
        if not result:
            raise HTTPException(status_code=404, detail="Device not found")
        return result
    except Exception as e:
        _handle_service_error(e, "get_device")


@router.post("/devices/bind-code")
async def generate_bind_code(
    current_user: Staff = Depends(get_current_active_user),
):
    """Generate a new bind code for device registration."""
    try:
        return await device_control_client.generate_bind_code(
            project_id=str(current_user.project_id),
        )
    except Exception as e:
        _handle_service_error(e, "generate_bind_code")


class DeviceUpdateRequest(BaseModel):
    """Request schema for updating a device."""

    device_name: Optional[str] = Field(None, description="Device name")
    ai_provider_id: Optional[str] = Field(
        None, description="AI Provider ID for this device"
    )
    model: Optional[str] = Field(
        None, description="LLM model identifier for this device"
    )


@router.patch("/devices/{device_id}")
async def update_device(
    device_id: str,
    request: DeviceUpdateRequest,
    current_user: Staff = Depends(get_current_active_user),
):
    """Update a device."""
    try:
        data = request.model_dump(exclude_unset=True)

        result = await device_control_client.update_device(
            device_id=device_id,
            project_id=str(current_user.project_id),
            data=data,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Device not found")
        return result
    except Exception as e:
        _handle_service_error(e, "update_device")


@router.delete("/devices/{device_id}")
async def delete_device(
    device_id: str,
    current_user: Staff = Depends(get_current_active_user),
):
    """Delete (unbind) a device."""
    try:
        success = await device_control_client.delete_device(
            device_id=device_id,
            project_id=str(current_user.project_id),
        )
        if not success:
            raise HTTPException(status_code=404, detail="Device not found")
        return {"success": True}
    except Exception as e:
        _handle_service_error(e, "delete_device")


@router.post("/devices/{device_id}/disconnect")
async def disconnect_device(
    device_id: str,
    current_user: Staff = Depends(get_current_active_user),
):
    """Force disconnect a device."""
    try:
        success = await device_control_client.disconnect_device(
            device_id=device_id,
            project_id=str(current_user.project_id),
        )
        if not success:
            raise HTTPException(status_code=404, detail="Device not found")
        return {"success": True}
    except Exception as e:
        _handle_service_error(e, "disconnect_device")


# Device Debug Chat Endpoints


@router.post("/chat")
async def device_debug_chat(
    request: DeviceDebugChatRequest,
    current_user: Staff = Depends(get_current_active_user),
):
    """Debug an explicitly selected, device-bound agent using standard AI SSE.

    Requires agent_id. Uses its saved model and tool-call limit unchanged.
    Events match /agents/run: connected, event, error; domain terminal
    events are workflow_completed and workflow_failed. No customer message is
    sent, and conversational memory is disabled for this isolated execution.
    """
    project_id = str(current_user.project_id)
    try:
        await validate_device_debug(request, project_id)
    except Exception as e:
        _handle_service_error(e, "device_debug_chat_verify")

    return StreamingResponse(
        stream_device_debug(request, project_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/connected-devices")
async def list_connected_devices(
    current_user: Staff = Depends(get_current_active_user),
):
    """List all connected devices available for agent control.

    Returns devices that are currently connected via TCP and can receive
    commands from the AI agent.
    """
    try:
        return await device_control_client.list_connected_devices(
            project_id=str(current_user.project_id)
        )
    except Exception as e:
        _handle_service_error(e, "list_connected_devices")


@router.get("/devices/{device_id}/tools")
async def get_device_tools(
    device_id: str,
    current_user: Staff = Depends(get_current_active_user),
):
    """Get available tools from a connected device.

    Returns the list of MCP tools available on the device for AI agent control.
    """
    # Verify device belongs to current user's project
    try:
        device = await device_control_client.get_device(
            device_id=device_id,
            project_id=str(current_user.project_id),
        )
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e, "get_device_tools_verify")

    try:
        result = await device_control_client.get_device_tools(
            device_id=device_id, project_id=str(current_user.project_id)
        )
        if not result:
            raise HTTPException(
                status_code=404,
                detail="Device not connected or tools unavailable",
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e, "get_device_tools")
