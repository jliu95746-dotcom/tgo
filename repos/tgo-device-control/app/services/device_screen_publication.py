"""Persist versioned screen scope without automatically granting business execution."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.device_read_policy import DeviceReadPolicy
from app.models.device_screen_policy import DeviceScreenPolicy
from app.models.device_script_policy import DeviceScriptPolicy
from app.services.device_script_connection import clear_script_confirmation
from app.schemas.device_screen_policy import (
    ScreenGrantStatus,
    ScreenPolicy,
    ScreenRegion,
)
from app.schemas.device_screen_publication import (
    ScreenLocalGrantRead,
    ScreenPolicyPublish,
    ScreenPolicyRead,
    ScreenPolicyRevoke,
)
from app.services.device_screen_policy import (
    apply_screen_policy,
    screen_policy_digest,
    validate_local_screen_scope,
)
from app.services.device_read_policy import clear_read_confirmation
from app.services.tcp_connection_manager import (
    TcpDeviceConnection,
    tcp_connection_manager,
)


def stored_screen_policy(row: DeviceScreenPolicy) -> ScreenPolicy:
    return ScreenPolicy(
        protocol_version=1,
        revision=row.revision,
        grant_id=row.grant_id,
        max_actions=row.max_actions,
        allow_click=row.allow_click,
        allow_text=row.allow_text,
        click_regions=[
            ScreenRegion.model_validate(region) for region in row.click_regions
        ],
        digest=row.digest,
    )


def clear_screen_confirmation(connection: TcpDeviceConnection) -> None:
    connection.desired_screen_policy = None
    connection.applied_screen_policy_digest = None
    connection.screen_grant = None


class DeviceScreenPublicationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _require_device(
        self, project_id: UUID, device_id: UUID, *, lock: bool = False
    ) -> None:
        query = select(Device.id).where(
            Device.id == device_id, Device.project_id == project_id
        )
        if lock:
            query = query.with_for_update()
        if await self.db.scalar(query) is None:
            raise HTTPException(404, "Device not found")

    async def _row(
        self, project_id: UUID, device_id: UUID
    ) -> DeviceScreenPolicy | None:
        return await self.db.scalar(
            select(DeviceScreenPolicy)
            .where(
                DeviceScreenPolicy.device_id == device_id,
                DeviceScreenPolicy.project_id == project_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )

    @staticmethod
    def _connection(project_id: UUID, device_id: UUID) -> TcpDeviceConnection | None:
        current = tcp_connection_manager.get_connection(str(device_id))
        return current if current and current.project_id == str(project_id) else None

    @staticmethod
    async def _grant(connection: TcpDeviceConnection) -> ScreenGrantStatus:
        raw = await connection.send_request("screen/status", {}, timeout=10)
        if tcp_connection_manager.get_connection(connection.agent_id) is not connection:
            raise HTTPException(409, "Device connection changed")
        try:
            return ScreenGrantStatus.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            clear_screen_confirmation(connection)
            raise HTTPException(409, "Local screen grant is unavailable") from None

    @staticmethod
    def _response(row: DeviceScreenPolicy) -> ScreenPolicyRead:
        connection = DeviceScreenPublicationService._connection(
            row.project_id, row.device_id
        )
        active = bool(
            row.enabled
            and connection
            and connection.connection_instance_id == row.connection_instance_id
            and connection.desired_screen_policy == stored_screen_policy(row)
            and connection.applied_screen_policy_digest == row.digest
            and connection.screen_grant
            and connection.screen_grant.active
            and connection.screen_grant.grant_id == row.grant_id
            and connection.desired_read_policy_digest is None
        )
        return ScreenPolicyRead(
            device_id=row.device_id,
            project_id=row.project_id,
            policy=stored_screen_policy(row),
            status="revoked" if not row.enabled else "active" if active else "pending",
            updated_at=row.updated_at,
            revoked_at=row.revoked_at,
        )

    async def local_grant(
        self, project_id: UUID, device_id: UUID
    ) -> ScreenLocalGrantRead:
        async with tcp_connection_manager.lifecycle_lock(str(device_id)):
            await self._require_device(project_id, device_id)
            connection = self._connection(project_id, device_id)
            if connection is None:
                return ScreenLocalGrantRead(
                    device_id=device_id,
                    project_id=project_id,
                    grant=ScreenGrantStatus(protocol_version=1, active=False),
                )
            return ScreenLocalGrantRead(
                device_id=device_id,
                project_id=project_id,
                connection_instance_id=connection.connection_instance_id,
                grant=await self._grant(connection),
            )

    async def get(self, project_id: UUID, device_id: UUID) -> ScreenPolicyRead | None:
        async with tcp_connection_manager.lifecycle_lock(str(device_id)):
            await self._require_device(project_id, device_id)
            row = await self._row(project_id, device_id)
            if row is None:
                return None
            connection = self._connection(project_id, device_id)
            if (
                row.enabled
                and connection
                and connection.connection_instance_id == row.connection_instance_id
            ):
                try:
                    grant = await self._grant(connection)
                    if not validate_local_screen_scope(
                        grant, stored_screen_policy(row)
                    ):
                        clear_screen_confirmation(connection)
                    else:
                        connection.screen_grant = grant
                except HTTPException:
                    clear_screen_confirmation(connection)
            return self._response(row)

    async def publish(
        self, project_id: UUID, device_id: UUID, request: ScreenPolicyPublish
    ) -> ScreenPolicyRead:
        async with tcp_connection_manager.lifecycle_lock(str(device_id)):
            # Device row serializes even first publication (no existing policy row).
            await self._require_device(project_id, device_id, lock=True)
            row = await self._row(project_id, device_id)
            if (row.revision if row else 0) != request.expected_policy_revision:
                raise HTTPException(409, "Device screen policy changed")
            read_policy = await self.db.scalar(
                select(DeviceReadPolicy).where(
                    DeviceReadPolicy.device_id == device_id,
                )
            )
            if (
                read_policy is not None
                and getattr(read_policy, "enabled", True) is not False
            ):
                raise HTTPException(
                    409, "Published file policy conflicts with screen mode"
                )
            script_policy = await self.db.scalar(
                select(DeviceScriptPolicy.device_id).where(
                    DeviceScriptPolicy.device_id == device_id,
                    DeviceScriptPolicy.enabled.is_(True),
                )
            )
            if script_policy is not None:
                raise HTTPException(
                    409, "Published script policy conflicts with screen mode"
                )
            connection = self._connection(project_id, device_id)
            if (
                connection is None
                or connection.connection_instance_id != request.connection_instance_id
                or (
                    connection.desired_read_policy_digest is not None
                    and (
                        read_policy is None
                        or read_policy.enabled is not False
                        or connection.desired_read_policy_digest != read_policy.digest
                    )
                )
            ):
                raise HTTPException(409, "Current local screen connection is required")
            grant = await self._grant(connection)
            scope = ScreenPolicy(
                protocol_version=1,
                revision=request.expected_policy_revision + 1,
                grant_id=request.grant_id,
                max_actions=request.max_actions,
                allow_click=request.allow_click,
                allow_text=request.allow_text,
                click_regions=request.click_regions,
                digest="0" * 64,
            )
            scope = scope.model_copy(update={"digest": screen_policy_digest(scope)})
            if not validate_local_screen_scope(grant, scope):
                raise HTTPException(
                    422, "Screen policy exceeds the current local grant"
                )
            if row is None:
                row = DeviceScreenPolicy(device_id=device_id, project_id=project_id)
                self.db.add(row)
            row.revision, row.enabled = scope.revision, True
            row.connection_instance_id, row.grant_id = (
                connection.connection_instance_id,
                scope.grant_id,
            )
            row.max_actions, row.allow_click, row.allow_text = (
                scope.max_actions,
                scope.allow_click,
                scope.allow_text,
            )
            row.click_regions = [region.model_dump() for region in scope.click_regions]
            row.digest, row.revoked_at = scope.digest, None
            clear_screen_confirmation(connection)
            try:
                await self.db.commit()
            except BaseException as error:
                await self.db.rollback()
                await self._close(connection)
                if isinstance(error, IntegrityError):
                    raise HTTPException(409, "Device screen policy changed") from None
                raise
            # Reacquire the durable version before RPC; another process may have
            # published/revoked while the commit released its lock.
            await self._require_device(project_id, device_id, lock=True)
            saved = await self._row(project_id, device_id)
            if saved is None or not saved.enabled or saved.digest != scope.digest:
                raise HTTPException(409, "Device screen policy changed")
            try:
                # Only an explicitly retired file policy may give way to this
                # freshly checked local window grant; its history remains.
                clear_read_confirmation(connection)
                clear_script_confirmation(connection)
                await apply_screen_policy(connection, scope)
                await self.db.commit()
                await self.db.refresh(saved)
            except BaseException:
                await self._close(connection)
                raise
            return self._response(saved)

    @staticmethod
    async def _close(connection: TcpDeviceConnection) -> None:
        clear_screen_confirmation(connection)
        await asyncio.shield(
            tcp_connection_manager.unregister_connection(
                connection.agent_id,
                expected_connection=connection,
            )
        )

    async def revoke(
        self, project_id: UUID, device_id: UUID, request: ScreenPolicyRevoke
    ) -> ScreenPolicyRead:
        async with tcp_connection_manager.lifecycle_lock(str(device_id)):
            await self._require_device(project_id, device_id, lock=True)
            row = await self._row(project_id, device_id)
            if row is None:
                raise HTTPException(404, "Device screen policy not found")
            if row.revision != request.expected_policy_revision:
                raise HTTPException(409, "Device screen policy changed")
            connection = self._connection(project_id, device_id)
            bound = bool(
                connection
                and connection.connection_instance_id == row.connection_instance_id
            )
            if bound and connection:
                clear_screen_confirmation(connection)
            try:
                if row.enabled:
                    row.revision += 1
                    row.enabled = False
                    row.revoked_at = datetime.now(timezone.utc)
                    row.digest = screen_policy_digest(stored_screen_policy(row))
                    await self.db.commit()
                    await self.db.refresh(row)
            finally:
                # Cancellation/commit failure cannot leave the old local grant
                # executable. A replacement connection must never be closed.
                if bound and connection:
                    await self._close(connection)
            return self._response(row)
