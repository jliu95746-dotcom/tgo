"""Only the exact signed AI execution may update session lifecycle."""

from uuid import UUID

from fastapi import Depends, HTTPException

from app.core.device_auth import require_service_principal
from app.schemas.device_access import DeviceServicePrincipal


async def require_session_writer(
    device_id: UUID,
    session_id: UUID,
    principal: DeviceServicePrincipal = Depends(require_service_principal),
) -> DeviceServicePrincipal:
    if (
        principal.sub != "tgo-ai"
        or principal.device_id != device_id
        or principal.session_id != session_id
    ):
        raise HTTPException(403, "Device execution scope mismatch")
    return principal
