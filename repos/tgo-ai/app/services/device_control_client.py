"""Device binding validation and short-lived bound-device MCP credentials."""

from datetime import datetime, timedelta, timezone
import re
from uuid import UUID, uuid4

import httpx
from jose import jwt
from pydantic import ValidationError as SchemaValidationError

from app.config import settings
from app.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.schemas.device_access import (
    BoundDeviceIdentity,
    BoundDeviceReadPolicy,
)
from app.schemas.device_session import (
    DeviceExecutionIdentity,
    DeviceFinishStatus,
    DeviceSessionReceipt,
)
from app.schemas.device_script_execution import (
    PreparedScriptCall,
    ScriptExecuteBody,
    ScriptExecutionRead,
    ScriptReceiptReference,
    validate_script_receipt,
    ScriptPolicyRead,
    validate_reviewed_script_configuration,
)
from app.schemas.device_script_policy import ScriptCatalog, ScriptDescriptor
from app.schemas.device_script_budget import ScriptBudgetRead


class DeviceControlClient:
    @staticmethod
    def headers(
        project_id: str | UUID,
        device_id: str | UUID,
        *,
        session_id: UUID | None = None,
        read_policy_digest: str | None = None,
        script_policy_digest: str | None = None,
    ) -> dict[str, str]:
        """Credentials are bound to one project and device."""
        project, device = UUID(str(project_id)), UUID(str(device_id))
        now = datetime.now(timezone.utc)
        claims: dict[str, str | datetime] = {
            "sub": "tgo-ai",
            "iss": "tgo-internal",
            "aud": "tgo-device-control",
            "project_id": str(project),
            "device_id": str(device),
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": uuid4().hex,
        }
        if session_id is not None:
            claims["session_id"] = str(session_id)
        if read_policy_digest is not None:
            if re.fullmatch(r"[0-9a-f]{64}", read_policy_digest) is None:
                raise ValueError("Invalid device read policy digest")
            claims["read_policy_digest"] = read_policy_digest
        if script_policy_digest is not None:
            if (
                session_id is None
                or read_policy_digest is not None
                or re.fullmatch(r"[0-9a-f]{64}", script_policy_digest) is None
            ):
                raise ValueError(
                    "Script credentials require one bound session"
                )
            claims["script_policy_digest"] = script_policy_digest
        token = jwt.encode(
            claims,
            settings.secret_key,
            algorithm="HS256",
        )
        return {"Authorization": "Bearer " + token}

    async def execute_script(
        self, call: PreparedScriptCall
    ) -> ScriptExecutionRead:
        return await self._script_request(call, execute=True)

    async def validate_script_configuration(
        self, project: UUID, device: UUID
    ) -> tuple[ScriptPolicyRead, ScriptDescriptor]:
        url = (
            f"{settings.device_control_service_url.rstrip('/')}"
            f"/v1/devices/{device}"
        )
        try:
            async with httpx.AsyncClient(
                timeout=10,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                headers = self.headers(project, device)
                response = await client.get(
                    url + "/script-policy", headers=headers
                )
                response.raise_for_status()
                policy = ScriptPolicyRead.model_validate(response.json())
                response = await client.get(
                    url + "/script-catalog", headers=headers
                )
                response.raise_for_status()
                catalog = ScriptCatalog.model_validate(response.json())
            item = next(
                (
                    row
                    for row in catalog.scripts
                    if row.script_id == policy.policy.script_id
                ),
                None,
            )
            if item is None:
                raise ValueError("Reviewed script is no longer available")
            return validate_reviewed_script_configuration(
                project, device, policy, item
            )
        except (httpx.HTTPError, SchemaValidationError, ValueError, TypeError):
            raise ExternalServiceError(
                "device-control",
                "设备脚本配置尚未就绪或已经变化，请重新核对",
            ) from None

    async def read_script(
        self, call: ScriptReceiptReference
    ) -> ScriptExecutionRead:
        return await self._script_request(call, execute=False)

    async def read_script_budget(
        self,
        project: UUID,
        device: UUID,
        policy_digest: str,
        grant_digest: str,
    ) -> ScriptBudgetRead:
        url = (
            f"{settings.device_control_service_url.rstrip('/')}"
            f"/v1/devices/{device}/script-budget"
        )
        try:
            if any(
                re.fullmatch(r"[0-9a-f]{64}", digest) is None
                for digest in (policy_digest, grant_digest)
            ):
                raise ValueError("Invalid expected script scope")
            async with httpx.AsyncClient(
                timeout=10, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.get(
                    url, headers=self.headers(project, device)
                )
            response.raise_for_status()
            raw = response.json()
            if not isinstance(raw, dict) or not {
                "scope",
                "can_enable",
            }.issubset(raw):
                raise ValueError("Missing budget observation scope")
            budget = ScriptBudgetRead.model_validate(raw)
            age = (
                datetime.now(timezone.utc) - budget.observed_at
            ).total_seconds()
            if (
                budget.project_id != project
                or budget.device_id != device
                or budget.policy_digest != policy_digest
                or budget.grant_digest != grant_digest
                or not -5 <= age <= 30
            ):
                raise ValueError("Script budget scope or freshness changed")
            return budget
        except (httpx.HTTPError, SchemaValidationError, ValueError, TypeError):
            raise ExternalServiceError(
                "device-control", "无法核验当前脚本剩余次数，请刷新配置后重试"
            ) from None

    async def _script_request(
        self, call: ScriptReceiptReference, *, execute: bool
    ) -> ScriptExecutionRead:
        identity = call.identity
        url = (
            f"{settings.device_control_service_url.rstrip('/')}"
            f"/v1/devices/{identity.device_id}"
            f"/script-executions/{call.execution_id}"
        )
        try:
            body = None
            if execute:
                if not isinstance(call, PreparedScriptCall):
                    raise ValueError("A query reference cannot execute")
                body = ScriptExecuteBody.model_validate_json(
                    call.parameters_json
                )
            async with httpx.AsyncClient(
                timeout=call.timeout_seconds + 15 if execute else 10,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.request(
                    "POST" if execute else "GET",
                    url,
                    json=body.model_dump() if body is not None else None,
                    headers=self.headers(
                        identity.project_id,
                        identity.device_id,
                        session_id=identity.session_id,
                        script_policy_digest=call.policy_digest,
                    ),
                )
            response.raise_for_status()
            receipt = ScriptExecutionRead.model_validate(response.json())
            return validate_script_receipt(call, receipt)
        except (httpx.HTTPError, SchemaValidationError, ValueError, TypeError):
            raise ExternalServiceError(
                "device-control",
                "脚本结果未确认，请保留原执行编号查询，勿重复提交",
            ) from None

    async def start_session(self, identity: DeviceExecutionIdentity) -> None:
        body = {
            "agent_id": str(identity.agent_id),
            "agent_name": identity.agent_name,
        }
        if identity.workflow_execution_id is not None:
            body["workflow_execution_id"] = str(identity.workflow_execution_id)
        await self._session_request(
            identity,
            "start",
            body,
        )

    async def heartbeat_session(
        self, identity: DeviceExecutionIdentity
    ) -> None:
        await self._session_request(identity, "heartbeat", {})

    async def finish_session(
        self,
        identity: DeviceExecutionIdentity,
        status: DeviceFinishStatus,
    ) -> None:
        await self._session_request(identity, "finish", {"status": status})

    async def _session_request(
        self,
        identity: DeviceExecutionIdentity,
        operation: str,
        body: dict[str, str],
    ) -> None:
        url = (
            f"{settings.device_control_service_url.rstrip('/')}/v1/sessions/"
            f"{identity.device_id}/{identity.session_id}/{operation}"
        )
        try:
            async with httpx.AsyncClient(
                timeout=5 if operation == "finish" else 10,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers=self.headers(
                        identity.project_id,
                        identity.device_id,
                        session_id=identity.session_id,
                    ),
                )
            response.raise_for_status()
            receipt = DeviceSessionReceipt.model_validate(response.json())
            if (
                receipt.id != identity.session_id
                or receipt.device_id != identity.device_id
                or receipt.agent_id != identity.agent_id
                or receipt.workflow_execution_id
                != identity.workflow_execution_id
            ):
                raise ValueError("Session identity mismatch")
            expected_status = (
                body["status"] if operation == "finish" else "running"
            )
            if receipt.status != expected_status:
                raise ValueError("Unexpected session status")
            if operation != "finish" and (
                receipt.lease_expires_at is None
                or receipt.lease_expires_at <= datetime.now(timezone.utc)
            ):
                raise ValueError("Session lease expired")
        except (httpx.HTTPError, SchemaValidationError, ValueError) as exc:
            raise ExternalServiceError(
                "device-control", "暂时无法保存设备执行记录，请稍后重试"
            ) from exc

    async def validate_binding(
        self, project_id: UUID, device_id: str
    ) -> BoundDeviceReadPolicy | None:
        try:
            device = UUID(device_id)
        except ValueError as exc:
            raise ValidationError("设备编号无效", "bound_device_id") from exc
        url = (
            f"{settings.device_control_service_url.rstrip('/')}"
            f"/v1/devices/{device}"
        )
        try:
            async with httpx.AsyncClient(
                timeout=10, follow_redirects=False
            ) as client:
                response = await client.get(
                    url, headers=self.headers(project_id, device)
                )
                if response.status_code in (403, 404):
                    raise NotFoundError("Device", device)
                response.raise_for_status()
                identity = BoundDeviceIdentity.model_validate(response.json())
                if identity.id != device or identity.project_id != project_id:
                    raise NotFoundError("Device", device)
                policy_response = await client.get(
                    url + "/read-policy",
                    headers=self.headers(project_id, device),
                )
            if policy_response.status_code == 404:
                if (
                    policy_response.json().get("detail")
                    == "Device read policy not found"
                ):
                    return None  # Legacy device: no published read policy.
                raise NotFoundError("Device", device)
            policy_response.raise_for_status()
            policy = BoundDeviceReadPolicy.model_validate(
                policy_response.json()
            )
            if policy.device_id != device or policy.project_id != project_id:
                raise NotFoundError("Device", device)
            if policy.status != "active" or not policy.verified:
                raise ValidationError("设备当前连接的文件读取测试尚未通过", "bound_device_id")
            return policy
        except (httpx.HTTPError, SchemaValidationError, ValueError) as exc:
            raise ExternalServiceError(
                "device-control", "暂时无法核验设备，请稍后重试"
            ) from exc


device_control_client = DeviceControlClient()
