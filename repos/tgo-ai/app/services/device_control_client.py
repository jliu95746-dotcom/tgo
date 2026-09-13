"""Device binding validation and short-lived bound-device MCP credentials."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
from jose import jwt
from pydantic import ValidationError as SchemaValidationError

from app.config import settings
from app.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.schemas.device_access import BoundDeviceIdentity
from app.schemas.device_session import (
    DeviceExecutionIdentity,
    DeviceFinishStatus,
    DeviceSessionReceipt,
)


class DeviceControlClient:
    @staticmethod
    def headers(
        project_id: str | UUID,
        device_id: str | UUID,
        *,
        session_id: UUID | None = None,
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
        token = jwt.encode(
            claims,
            settings.secret_key,
            algorithm="HS256",
        )
        return {"Authorization": "Bearer " + token}

    async def start_session(self, identity: DeviceExecutionIdentity) -> None:
        await self._session_request(
            identity,
            "start",
            {
                "agent_id": str(identity.agent_id),
                "agent_name": identity.agent_name,
            },
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

    async def validate_binding(self, project_id: UUID, device_id: str) -> None:
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
        except (httpx.HTTPError, SchemaValidationError, ValueError) as exc:
            raise ExternalServiceError(
                "device-control", "暂时无法核验设备，请稍后重试"
            ) from exc


device_control_client = DeviceControlClient()
