"""Project-scoped internal authentication for device management and tool access."""

from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_async_db
from app.schemas.device import DeviceResponse
from app.schemas.device_access import DeviceServicePrincipal
from app.services.device_service import DeviceService

service_bearer = HTTPBearer(auto_error=False)


async def require_service_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(service_bearer),
) -> DeviceServicePrincipal:
    """Accept only short-lived credentials issued for this internal service."""
    if credentials is None:
        raise HTTPException(401, "Device service authentication required")
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.SECRET_KEY,
            algorithms=["HS256"],
            audience="tgo-device-control",
            issuer="tgo-internal",
            options={
                "require_exp": True,
                "require_iat": True,
                "require_aud": True,
                "require_iss": True,
                "require_sub": True,
            },
        )
        principal = DeviceServicePrincipal.model_validate(claims)
        if principal.sub == "tgo-ai" and principal.device_id is None:
            raise ValueError("AI access requires a bound device")
        return principal
    except (JWTError, ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(401, "Invalid device service credentials") from exc


async def require_management_project(
    request: Request,
    principal: DeviceServicePrincipal = Depends(require_service_principal),
    project_id: UUID | None = Query(None),
) -> UUID:
    """A query parameter may narrow/confirm a signed project, never replace it."""
    if project_id is not None and project_id != principal.project_id:
        raise HTTPException(403, "Device project scope mismatch")
    if principal.device_id is not None:
        if request.path_params.get("device_id") != str(principal.device_id):
            raise HTTPException(403, "Device binding scope mismatch")
    if principal.sub == "tgo-ai" and request.method != "GET":
        raise HTTPException(403, "AI device access cannot manage devices")
    return principal.project_id


async def require_bound_device(
    device_id: UUID,
    principal: DeviceServicePrincipal = Depends(require_service_principal),
    db: AsyncSession = Depends(get_async_db),
) -> DeviceResponse:
    """Recheck persisted ownership for every MCP request, including discovery."""
    if principal.device_id is not None and device_id != principal.device_id:
        raise HTTPException(403, "Device binding scope mismatch")
    device = await DeviceService(db).get_device(device_id, principal.project_id)
    if device is None:
        raise HTTPException(404, "Device not found")
    return device
