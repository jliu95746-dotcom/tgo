"""Fetch reviewed metadata without execution or policy changes."""

from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.schemas.device_script_policy import ScriptCatalog
from app.services.tcp_connection_manager import (
    TcpDeviceConnection,
    tcp_connection_manager,
)


class DeviceScriptCatalogService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _require_owner(self, project_id: UUID, device_id: UUID) -> None:
        owned = await self.db.scalar(
            select(Device.id).where(
                Device.id == device_id,
                Device.project_id == project_id,
            )
        )
        if owned is None:
            raise HTTPException(404, "Device not found")

    @staticmethod
    def _matches(
        connection: TcpDeviceConnection | None,
        project_id: UUID,
        device_id: UUID,
    ) -> bool:
        return bool(
            connection is not None
            and connection.agent_id == str(device_id)
            and connection.device_db_id == str(device_id)
            and connection.project_id == str(project_id)
            and not connection.writer.is_closing()
        )

    async def get_catalog(
        self, project_id: UUID, device_id: UUID
    ) -> ScriptCatalog:
        await self._require_owner(project_id, device_id)
        connection = tcp_connection_manager.get_connection(str(device_id))
        if connection is None or not self._matches(
            connection, project_id, device_id
        ):
            raise HTTPException(409, "Device script catalog is not connected")
        instance = connection.connection_instance_id
        response = await connection.send_request(
            "scripts/list", {}, timeout=10
        )
        await self._require_owner(project_id, device_id)
        if (
            tcp_connection_manager.get_connection(str(device_id))
            is not connection
            or connection.connection_instance_id != instance
            or not self._matches(connection, project_id, device_id)
        ):
            raise HTTPException(
                409, "Device connection changed; refresh script catalog"
            )
        if response is None or (
            isinstance(response, dict) and "error" in response
        ):
            raise HTTPException(
                409, "Device cannot provide a reviewed script catalog"
            )
        try:
            return ScriptCatalog.model_validate(response)
        except ValidationError as exc:
            raise HTTPException(
                502, "Invalid reviewed script catalog from device"
            ) from exc
