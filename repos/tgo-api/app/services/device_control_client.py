"""HTTP client for the device-control service."""

from typing import Any, Dict, Optional, TypeVar
from uuid import UUID
import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.logging import get_logger
from app.services.device_access import device_service_headers
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.schemas.device_session import DeviceSessionDetail, DeviceSessionList

SessionRead = TypeVar("SessionRead", bound=BaseModel)

logger = get_logger("services.device_control_client")


class DeviceControlClient:
    """HTTP client for communicating with tgo-device-control service."""

    def __init__(self):
        self.base_url = settings.DEVICE_CONTROL_SERVICE_URL.rstrip("/")
        self.timeout = settings.DEVICE_CONTROL_SERVICE_TIMEOUT

    async def _request(
        self,
        method: str,
        path: str,
        project_id: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: float | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Make an HTTP request to the device control service."""
        url = f"{self.base_url}{path}"

        async with httpx.AsyncClient(
            timeout=self.timeout if timeout is None else timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json,
                    headers=device_service_headers(project_id),
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Device control HTTP error: {e.response.status_code}"
                )
                raise
            except httpx.RequestError as e:
                logger.error(
                    f"Device control connection error: {type(e).__name__}"
                )
                raise

    async def _read_session_metadata(
        self,
        path: str,
        project_id: str,
        params: dict[str, str | int],
        schema: type[SessionRead],
    ) -> SessionRead:
        try:
            result = await self._request(
                "GET",
                path,
                project_id,
                params=params,
                timeout=10,
            )
            return schema.model_validate(result)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise NotFoundError("设备执行记录") from None
            raise ExternalServiceError(
                "device-control", "暂时无法读取设备执行记录，请稍后重试"
            ) from None
        except (httpx.RequestError, ValidationError, ValueError):
            raise ExternalServiceError(
                "device-control", "暂时无法读取设备执行记录，请稍后重试"
            ) from None

    async def list_sessions(
        self,
        project_id: str,
        *,
        device_id: UUID | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> DeviceSessionList:
        params: dict[str, str | int] = {"skip": skip, "limit": limit}
        if device_id is not None:
            params["device_id"] = str(device_id)
        result = await self._read_session_metadata(
            "/v1/sessions",
            project_id,
            params,
            DeviceSessionList,
        )
        if device_id is not None and any(
            row.device_id != device_id for row in result.sessions
        ):
            raise ExternalServiceError("device-control", "设备执行记录不匹配")
        return result

    async def get_session(
        self,
        session_id: UUID,
        project_id: str,
        *,
        step_skip: int = 0,
        step_limit: int = 100,
    ) -> DeviceSessionDetail:
        result = await self._read_session_metadata(
            f"/v1/sessions/{session_id}",
            project_id,
            {"step_skip": step_skip, "step_limit": step_limit},
            DeviceSessionDetail,
        )
        if result.id != session_id:
            raise ExternalServiceError("device-control", "设备执行记录不匹配")
        return result

    # Device Management

    async def list_devices(
        self,
        project_id: str,
        device_type: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """List devices for a project."""
        params = {
            "project_id": project_id,
            "skip": skip,
            "limit": limit,
        }
        if device_type:
            params["device_type"] = device_type
        if status:
            params["status"] = status

        return await self._request(
            "GET", "/v1/devices", project_id, params=params
        )

    async def get_device(
        self,
        device_id: str,
        project_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific device."""
        params = {"project_id": project_id}
        return await self._request(
            "GET", f"/v1/devices/{device_id}", project_id, params=params
        )

    async def generate_bind_code(self, project_id: str) -> Dict[str, Any]:
        """Generate a bind code for device registration."""
        params = {"project_id": project_id}
        return await self._request(
            "POST", "/v1/devices/bind-code", project_id, params=params
        )

    async def update_device(
        self,
        device_id: str,
        project_id: str,
        data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update a device."""
        params = {"project_id": project_id}
        return await self._request(
            "PATCH",
            f"/v1/devices/{device_id}",
            project_id,
            params=params,
            json=data,
        )

    async def delete_device(
        self,
        device_id: str,
        project_id: str,
    ) -> bool:
        """Delete a device."""
        params = {"project_id": project_id}
        await self._request(
            "DELETE", f"/v1/devices/{device_id}", project_id, params=params
        )
        return True

    async def disconnect_device(
        self,
        device_id: str,
        project_id: str,
    ) -> bool:
        """Force disconnect a device."""
        params = {"project_id": project_id}
        await self._request(
            "POST",
            f"/v1/devices/{device_id}/disconnect",
            project_id,
            params=params,
        )
        return True

    async def get_device_tools(
        self,
        device_id: str,
        project_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get available tools from a connected device."""
        return await self._request(
            "GET", f"/v1/mcp/tools/{device_id}", project_id
        )

    async def list_connected_devices(self, project_id: str) -> Dict[str, Any]:
        """List all connected devices available for agent control."""
        return await self._request("GET", "/v1/devices/connected", project_id)


# Global singleton instance
device_control_client = DeviceControlClient()
