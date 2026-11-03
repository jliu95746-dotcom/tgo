"""HTTP client for the device-control service."""

from typing import Any, Dict, Optional, TypeVar
from uuid import UUID
import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.logging import get_logger
from app.services.device_access import device_service_headers
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.schemas.device_session import DeviceSessionDetail, DeviceSessionList
from app.schemas.device_scenario import (
    DeviceScenario, DeviceScenarioDraftRead, DeviceScenarioDraftWrite,
)
from app.schemas.device_read_policy import (
    DeviceReadPolicyPublish,
    DeviceReadPolicyRead,
    DeviceReadPolicyRevoke,
    DeviceReadPolicyTest,
)
from app.schemas.device_screen_publication import (
    ScreenLocalGrantRead, ScreenPolicyPublish, ScreenPolicyRead, ScreenPolicyRevoke,
)
from app.schemas.device_script_policy import ScriptCatalog
from app.schemas.device_script_publication import (
    ScriptLocalStatusRead,
    ScriptPolicyPublish,
    ScriptPolicyRead,
    ScriptPolicyRevoke,
)

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
        device_id: UUID | None = None,
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
                    headers=(
                        device_service_headers(project_id, device_id=device_id)
                        if device_id is not None
                        else device_service_headers(project_id)
                    ),
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

    async def _script_management(
        self,
        method: str,
        suffix: str,
        device: UUID,
        project: str,
        schema: type[SessionRead],
        body: BaseModel | None = None,
    ) -> SessionRead:
        try:
            raw = await self._request(
                method,
                f"/v1/devices/{device}/{suffix}",
                project,
                json=(
                    body.model_dump(mode="json")
                    if body is not None else None
                ),
                device_id=device,
            )
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            messages = {
                400: "脚本配置请求无效",
                403: "没有访问该设备脚本配置的权限",
                404: "设备或脚本配置不存在",
                409: "设备连接或策略已变化，请刷新后重试",
                422: "脚本配置超出本机审核范围",
            }
            if code in messages:
                raise HTTPException(code, messages[code]) from None
            raise ExternalServiceError(
                "device-control", "脚本管理服务暂不可用"
            ) from None
        except (httpx.RequestError, ValueError):
            raise ExternalServiceError(
                "device-control", "脚本管理服务暂不可用"
            ) from None
        try:
            result = schema.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            raise ExternalServiceError(
                "device-control", "脚本管理响应无效"
            ) from None
        if isinstance(result, (ScriptLocalStatusRead, ScriptPolicyRead)) and (
            result.device_id != device or result.project_id != UUID(project)
        ):
            raise ExternalServiceError(
                "device-control", "脚本配置归属不匹配"
            )
        return result

    async def get_script_catalog(
        self, device: UUID, project: str
    ) -> ScriptCatalog:
        return await self._script_management(
            "GET", "script-catalog", device, project, ScriptCatalog
        )

    async def get_script_status(
        self, device: UUID, project: str
    ) -> ScriptLocalStatusRead:
        return await self._script_management(
            "GET", "script-status", device, project, ScriptLocalStatusRead
        )

    async def get_script_policy(
        self, device: UUID, project: str
    ) -> ScriptPolicyRead:
        return await self._script_management(
            "GET", "script-policy", device, project, ScriptPolicyRead
        )

    async def publish_script_policy(
        self, device: UUID, project: str, request: ScriptPolicyPublish
    ) -> ScriptPolicyRead:
        result = await self._script_management(
            "PUT", "script-policy", device, project, ScriptPolicyRead, request
        )
        if (
            result.policy.revision != request.expected_policy_revision + 1
            or result.policy.script_id != request.script_id
            or result.policy.grant_digest != request.grant_digest
            or result.policy.timeout_seconds != request.timeout_seconds
            or result.policy.max_output_bytes != request.max_output_bytes
            or result.policy.max_runs != request.max_runs
            or result.status == "revoked"
        ):
            raise ExternalServiceError(
                "device-control", "脚本发布内容与本次配置不匹配"
            )
        return result

    async def revoke_script_policy(
        self, device: UUID, project: str, request: ScriptPolicyRevoke
    ) -> ScriptPolicyRead:
        result = await self._script_management(
            "POST", "script-policy/revoke", device, project,
            ScriptPolicyRead, request,
        )
        if (
            result.policy.revision != request.expected_policy_revision + 1
            or result.status != "revoked"
            or result.revoked_at is None
        ):
            raise ExternalServiceError(
                "device-control", "脚本撤销状态与本次配置不匹配"
            )
        return result

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
        workflow_execution_id: UUID | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> DeviceSessionList:
        params: dict[str, str | int] = {"skip": skip, "limit": limit}
        if device_id is not None:
            params["device_id"] = str(device_id)
        if workflow_execution_id is not None:
            params["workflow_execution_id"] = str(workflow_execution_id)
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
        if workflow_execution_id is not None and any(
            row.workflow_execution_id != workflow_execution_id
            for row in result.sessions
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

    async def get_screen_grant(
        self, device_id: UUID, project_id: str
    ) -> ScreenLocalGrantRead:
        result = await self._request(
            "GET", f"/v1/devices/{device_id}/screen-grant", project_id
        )
        try:
            grant = ScreenLocalGrantRead.model_validate(result)
        except ValidationError:
            raise ExternalServiceError("device-control", "设备本机授权响应无效") from None
        if grant.device_id != device_id or grant.project_id != UUID(project_id):
            raise ExternalServiceError("device-control", "设备本机授权归属不匹配")
        return grant

    async def get_screen_policy(
        self, device_id: UUID, project_id: str
    ) -> ScreenPolicyRead:
        result = await self._request(
            "GET", f"/v1/devices/{device_id}/screen-policy", project_id
        )
        return self._validate_screen_policy(result, device_id, project_id)

    async def publish_screen_policy(
        self, device_id: UUID, project_id: str, request: ScreenPolicyPublish
    ) -> ScreenPolicyRead:
        result = await self._request(
            "PUT",
            f"/v1/devices/{device_id}/screen-policy",
            project_id,
            json=request.model_dump(mode="json"),
        )
        return self._validate_screen_policy(result, device_id, project_id)

    async def revoke_screen_policy(
        self, device_id: UUID, project_id: str, request: ScreenPolicyRevoke
    ) -> ScreenPolicyRead:
        result = await self._request(
            "POST",
            f"/v1/devices/{device_id}/screen-policy/revoke",
            project_id,
            json=request.model_dump(mode="json"),
        )
        return self._validate_screen_policy(result, device_id, project_id)

    @staticmethod
    def _validate_screen_policy(
        result: object, device_id: UUID, project_id: str
    ) -> ScreenPolicyRead:
        try:
            policy = ScreenPolicyRead.model_validate(result)
        except ValidationError:
            raise ExternalServiceError("device-control", "设备屏幕策略响应无效") from None
        if policy.device_id != device_id or policy.project_id != UUID(project_id):
            raise ExternalServiceError("device-control", "设备屏幕策略归属不匹配")
        return policy

    async def revoke_read_policy(
        self, device_id: UUID, project_id: str, request: DeviceReadPolicyRevoke
    ) -> DeviceReadPolicyRead:
        result = await self._request(
            "POST",
            f"/v1/devices/{device_id}/read-policy/revoke",
            project_id,
            json=request.model_dump(mode="json"),
        )
        return self._validate_read_policy(result, device_id, project_id)

    async def get_read_policy(
        self, device_id: UUID, project_id: str,
    ) -> DeviceReadPolicyRead:
        result = await self._request(
            "GET", f"/v1/devices/{device_id}/read-policy", project_id,
        )
        return self._validate_read_policy(result, device_id, project_id)

    async def publish_read_policy(
        self, device_id: UUID, project_id: str, request: DeviceReadPolicyPublish,
    ) -> DeviceReadPolicyRead:
        result = await self._request(
            "PUT", f"/v1/devices/{device_id}/read-policy", project_id,
            json=request.model_dump(),
        )
        return self._validate_read_policy(result, device_id, project_id)

    async def test_read_policy(
        self, device_id: UUID, project_id: str, request: DeviceReadPolicyTest,
    ) -> DeviceReadPolicyRead:
        result = await self._request(
            "POST", f"/v1/devices/{device_id}/read-policy/test", project_id,
            json=request.model_dump(),
        )
        return self._validate_read_policy(result, device_id, project_id)

    @staticmethod
    def _validate_read_policy(
        result: Optional[Dict[str, Any]], device_id: UUID, project_id: str,
    ) -> DeviceReadPolicyRead:
        try:
            policy = DeviceReadPolicyRead.model_validate(result)
        except ValidationError:
            raise ExternalServiceError(
                "device-control", "设备只读策略响应无效"
            ) from None
        if policy.device_id != device_id or policy.project_id != UUID(project_id):
            raise ExternalServiceError("device-control", "设备只读策略归属不匹配")
        return policy

    async def get_scenario_draft(
        self, device_id: UUID, scenario: DeviceScenario, project_id: str,
    ) -> DeviceScenarioDraftRead:
        path = f"/v1/devices/{device_id}/scenario-drafts/{scenario.value}"
        result = await self._request(
            "GET", path, project_id,
        )
        return self._validate_scenario_draft(
            result, device_id, scenario, project_id
        )

    async def save_scenario_draft(
        self, device_id: UUID, scenario: DeviceScenario, project_id: str,
        request: DeviceScenarioDraftWrite,
    ) -> DeviceScenarioDraftRead:
        path = f"/v1/devices/{device_id}/scenario-drafts/{scenario.value}"
        result = await self._request(
            "PUT", path, project_id,
            json=request.model_dump(exclude_unset=True),
        )
        return self._validate_scenario_draft(
            result, device_id, scenario, project_id
        )

    @staticmethod
    def _validate_scenario_draft(
        result: Optional[Dict[str, Any]], device_id: UUID,
        scenario: DeviceScenario, project_id: str,
    ) -> DeviceScenarioDraftRead:
        try:
            draft = DeviceScenarioDraftRead.model_validate(result)
        except ValidationError:
            raise ExternalServiceError(
                "device-control", "设备场景草稿响应无效"
            ) from None
        if (
            draft.project_id != UUID(project_id)
            or draft.device_id != device_id
            or draft.scenario != scenario
        ):
            raise ExternalServiceError("device-control", "设备场景草稿归属不匹配")
        return draft

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
