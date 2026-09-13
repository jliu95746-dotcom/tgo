"""Device Service - Database operations for devices."""

import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple, List

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.device import Device, DeviceSession, DeviceStatus, DeviceType
from app.schemas.device import (
    ConnectedDeviceListResponse,
    ConnectedDeviceResponse,
    DeviceResponse,
    DeviceUpdateRequest,
    DeviceStatus as ResponseDeviceStatus,
)
from app.services.bind_code_service import bind_code_service
from app.services.tcp_connection_manager import TcpDeviceConnection, tcp_connection_manager

logger = get_logger("services.device_service")


class DeviceService:
    """Service for device database operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _connections(project_id: uuid.UUID) -> dict[uuid.UUID, TcpDeviceConnection]:
        return {
            uuid.UUID(connection.agent_id): connection
            for connection in tcp_connection_manager.list_connections()
            if connection.project_id == str(project_id)
        }

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    def _device_response(self, device: Device, connections: dict[uuid.UUID, TcpDeviceConnection]) -> DeviceResponse:
        response = DeviceResponse.model_validate(device)
        connection = connections.get(device.id)
        response.status = ResponseDeviceStatus.ONLINE if connection else ResponseDeviceStatus.OFFLINE
        if connection:
            response.last_seen_at = self._as_utc(connection.last_seen)
        return response

    async def list_connected_devices(self, project_id: uuid.UUID) -> ConnectedDeviceListResponse:
        connections = self._connections(project_id)
        if not connections:
            return ConnectedDeviceListResponse(devices=[], count=0)
        result = await self.db.execute(
            select(Device).where(Device.project_id == project_id, Device.id.in_(connections))
        )
        devices = [
            ConnectedDeviceResponse(
                device_id=device.id,
                project_id=project_id,
                name=device.device_name,
                version=connections[device.id].version,
                capabilities=connections[device.id].capabilities,
                tools_count=len(connections[device.id].tools),
                connected_at=self._as_utc(connections[device.id].connected_at),
                last_seen=self._as_utc(connections[device.id].last_seen),
            )
            for device in result.scalars().all()
        ]
        return ConnectedDeviceListResponse(devices=devices, count=len(devices))

    async def list_devices(
        self,
        project_id: uuid.UUID,
        device_type: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[DeviceResponse], int]:
        """List devices for a project."""
        # Build query
        conditions = [Device.project_id == project_id]
        connections = self._connections(project_id)

        if device_type:
            conditions.append(Device.device_type == device_type)
        if status == "online":
            conditions.append(Device.id.in_(connections))
        elif status == "offline":
            conditions.append(Device.id.not_in(connections))

        # Count total
        count_query = select(func.count(Device.id)).where(and_(*conditions))
        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        # Get devices
        query = select(Device).where(and_(*conditions)).order_by(Device.created_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(query)
        devices = result.scalars().all()

        return [self._device_response(d, connections) for d in devices], total

    async def get_device(
        self,
        device_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> Optional[DeviceResponse]:
        """Get a device by ID."""
        query = select(Device).where(and_(Device.id == device_id, Device.project_id == project_id))
        result = await self.db.execute(query)
        device = result.scalar_one_or_none()

        if device:
            return self._device_response(device, self._connections(project_id))
        return None

    async def generate_bind_code(
        self,
        project_id: uuid.UUID,
    ) -> Tuple[str, datetime]:
        """Generate a new bind code using Redis."""
        return await bind_code_service.generate(project_id)

    async def register_device(
        self,
        bind_code: str,
        device_name: str,
        device_type: str,
        os: str,
        os_version: Optional[str],
        screen_resolution: Optional[str],
    ) -> Optional[Device]:
        """Register a device using a bind code."""
        # Validate bind code from Redis
        project_id = await bind_code_service.validate(bind_code)
        if not project_id:
            logger.warning("Invalid or expired device bind code")
            return None

        logger.info(f"[DEBUG] Bind code valid, project_id={project_id}")

        # Create new device record
        try:
            device = Device(
                project_id=project_id,
                device_name=device_name,
                device_type=DeviceType(device_type),
                os=os,
                os_version=os_version,
                screen_resolution=screen_resolution,
                status=DeviceStatus.OFFLINE,
                device_token=str(uuid.uuid4()),
            )
            logger.info(f"[DEBUG] Device object created: {device}")

            self.db.add(device)
            logger.debug("Device added to session, committing")
            await self.db.commit()
            logger.debug("Device commit successful, refreshing")
            await self.db.refresh(device)

            logger.info(
                f"[DEBUG] Device registered successfully: {device_name} ({device.id}) for project {project_id}"
            )
            return device
        except Exception as e:
            logger.error("Device registration failed: %s", type(e).__name__)
            raise

    async def update_device(
        self,
        device_id: uuid.UUID,
        project_id: uuid.UUID,
        update_data: DeviceUpdateRequest,
    ) -> Optional[DeviceResponse]:
        """Update a device."""
        query = select(Device).where(and_(Device.id == device_id, Device.project_id == project_id))
        result = await self.db.execute(query)
        device = result.scalar_one_or_none()

        if not device:
            return None

        # Update fields
        if update_data.device_name is not None:
            device.device_name = update_data.device_name

        await self.db.commit()
        await self.db.refresh(device)

        return self._device_response(device, self._connections(project_id))

    async def update_device_status(
        self,
        device_id: uuid.UUID,
        status: DeviceStatus,
    ) -> None:
        """Update device status."""
        query = select(Device).where(Device.id == device_id)
        result = await self.db.execute(query)
        device = result.scalar_one_or_none()

        if device:
            device.status = status
            if status == DeviceStatus.ONLINE:
                device.last_seen_at = datetime.now(timezone.utc)
            await self.db.commit()

    async def delete_device(
        self,
        device_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> bool:
        """Revoke the token and close its connection as one lifecycle operation."""
        async with tcp_connection_manager.lifecycle_lock(str(device_id)):
            query = select(Device).where(and_(Device.id == device_id, Device.project_id == project_id))
            result = await self.db.execute(query)
            device = result.scalar_one_or_none()
            if not device:
                return False
            await self.db.delete(device)
            await self.db.commit()
            await tcp_connection_manager.unregister_connection(str(device_id))

        logger.info(f"Device deleted: {device_id}")
        return True

    async def get_device_by_token(self, device_token: str) -> Optional[Device]:
        """Get a device by its token (for reconnection)."""
        query = select(Device).where(Device.device_token == device_token)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def disconnect_device(self, device_id: uuid.UUID, project_id: uuid.UUID) -> bool:
        """Disconnect an owned device without revoking its reconnect token."""
        async with tcp_connection_manager.lifecycle_lock(str(device_id)):
            query = select(Device).where(and_(Device.id == device_id, Device.project_id == project_id))
            result = await self.db.execute(query)
            device = result.scalar_one_or_none()
            if not device:
                return False
            device.status = DeviceStatus.OFFLINE
            await self.db.commit()
            await tcp_connection_manager.unregister_connection(str(device_id))
            return True

    # Session management

    async def create_session(
        self,
        device_id: uuid.UUID,
        agent_id: Optional[uuid.UUID] = None,
    ) -> DeviceSession:
        """Create a new device session."""
        session = DeviceSession(
            device_id=device_id,
            agent_id=agent_id,
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def end_session(self, session_id: uuid.UUID) -> None:
        """End a device session."""
        query = select(DeviceSession).where(DeviceSession.id == session_id)
        result = await self.db.execute(query)
        session = result.scalar_one_or_none()

        if session:
            session.ended_at = datetime.now(timezone.utc)
            await self.db.commit()

    async def increment_session_stats(
        self,
        session_id: uuid.UUID,
        screenshots: int = 0,
        actions: int = 0,
    ) -> None:
        """Increment session statistics."""
        query = select(DeviceSession).where(DeviceSession.id == session_id)
        result = await self.db.execute(query)
        session = result.scalar_one_or_none()

        if session:
            session.screenshots_count += screenshots
            session.actions_count += actions
            await self.db.commit()
