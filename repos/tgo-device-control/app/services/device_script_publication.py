"""Persist script policies under the device lock shared with other modes."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.device_read_policy import DeviceReadPolicy
from app.models.device_screen_policy import DeviceScreenPolicy
from app.models.device_script_policy import DeviceScriptPolicy
from app.schemas.device_script_policy import (
    ScriptPolicy,
    ScriptSessionStatus,
    script_policy_digest,
)
from app.schemas.device_script_publication import (
    ScriptLocalStatusRead,
    ScriptPolicyPublish,
    ScriptPolicyRead,
    ScriptPolicyRevoke,
)
from app.services.device_read_policy import clear_read_confirmation
from app.services.device_script_connection import (
    apply_script_policy,
    clear_script_confirmation,
    connection_is_current,
    reviewed_script,
    script_status,
    validate_script_scope,
)
from app.services.tcp_connection_manager import (
    TcpDeviceConnection,
    tcp_connection_manager,
)


def make_script_policy(
    revision: int,
    *,
    script_id: str,
    grant_digest: str,
    timeout_seconds: int,
    max_output_bytes: int,
    max_runs: int,
) -> ScriptPolicy:
    draft = ScriptPolicy.model_construct(
        protocol_version=1,
        revision=revision,
        script_id=script_id,
        grant_digest=grant_digest,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        max_runs=max_runs,
        digest="",
    )
    # Construct for hashing only; fully validate the returned value.
    return ScriptPolicy.model_validate(
        {**draft.model_dump(), "digest": script_policy_digest(draft)}
    )


def stored_script_policy(row: DeviceScriptPolicy) -> ScriptPolicy:
    return ScriptPolicy(
        protocol_version=1,
        revision=row.revision,
        script_id=row.script_id,
        grant_digest=row.grant_digest,
        timeout_seconds=row.timeout_seconds,
        max_output_bytes=row.max_output_bytes,
        max_runs=row.max_runs,
        digest=row.digest,
    )


class DeviceScriptPublicationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _require_device(
        self, project: UUID, device: UUID, *, lock: bool = False
    ) -> None:
        query = select(Device.id).where(
            Device.id == device, Device.project_id == project
        )
        if lock:
            query = query.with_for_update()
        if await self.db.scalar(query) is None:
            raise HTTPException(404, "Device not found")

    async def _row(
        self, project: UUID, device: UUID
    ) -> DeviceScriptPolicy | None:
        return await self.db.scalar(
            select(DeviceScriptPolicy)
            .where(
                DeviceScriptPolicy.device_id == device,
                DeviceScriptPolicy.project_id == project,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )

    @staticmethod
    def _connection(project: UUID, device: UUID) -> TcpDeviceConnection | None:
        connection = tcp_connection_manager.get_connection(str(device))
        return (
            connection
            if (
                connection
                and connection.project_id == str(project)
                and connection.agent_id == str(device)
                and connection_is_current(connection)
            )
            else None
        )

    @staticmethod
    def _response(row: DeviceScriptPolicy) -> ScriptPolicyRead:
        connection = DeviceScriptPublicationService._connection(
            row.project_id, row.device_id
        )
        policy = stored_script_policy(row)
        ack = connection.applied_script_policy_ack if connection else None
        active = bool(
            row.enabled
            and connection
            and connection.script_policy_required
            and connection.desired_script_policy == policy
            and ack
            and ack.session_id == connection.script_session_id
            and ack.revision == policy.revision
            and ack.digest == policy.digest
            and connection.desired_read_policy_digest is None
            and connection.desired_screen_policy is None
        )
        return ScriptPolicyRead(
            project_id=row.project_id,
            device_id=row.device_id,
            policy=policy,
            status="revoked"
            if not row.enabled
            else "active"
            if active
            else "pending",
            updated_at=row.updated_at,
            revoked_at=row.revoked_at,
        )

    async def local_status(
        self, project: UUID, device: UUID
    ) -> ScriptLocalStatusRead:
        async with tcp_connection_manager.lifecycle_lock(str(device)):
            await self._require_device(project, device)
            connection = self._connection(project, device)
            status = (
                await script_status(connection)
                if connection
                else ScriptSessionStatus(
                    protocol_version=1,
                    state="unavailable",
                    reason="not_connected",
                )
            )
            return ScriptLocalStatusRead(
                project_id=project,
                device_id=device,
                session=status,
                connection_instance_id=connection.connection_instance_id
                if connection
                else None,
            )

    async def get(
        self, project: UUID, device: UUID
    ) -> ScriptPolicyRead | None:
        async with tcp_connection_manager.lifecycle_lock(str(device)):
            await self._require_device(project, device)
            row = await self._row(project, device)
            if row is None:
                return None
            connection = self._connection(project, device)
            if row.enabled and connection:
                try:
                    status = await script_status(connection)
                    if (
                        status.state != "active"
                        or status.session_id != connection.script_session_id
                    ):
                        connection.applied_script_policy_ack = None
                    else:
                        item = await reviewed_script(connection, row.script_id)
                        if not validate_script_scope(
                            item, stored_script_policy(row)
                        ):
                            connection.applied_script_policy_ack = None
                except HTTPException:
                    connection.applied_script_policy_ack = None
            return self._response(row)

    async def _no_other_mode(self, device: UUID) -> None:
        for model in (DeviceReadPolicy, DeviceScreenPolicy):
            active = await self.db.scalar(
                select(model.device_id).where(
                    model.device_id == device, model.enabled.is_(True)
                )
            )
            if active is not None:
                raise HTTPException(
                    409,
                    "Published file or screen policy conflicts "
                    "with script mode",
                )

    @staticmethod
    async def _close(connection: TcpDeviceConnection) -> None:
        clear_script_confirmation(connection, required=True)
        await asyncio.shield(
            tcp_connection_manager.unregister_connection(
                connection.agent_id, expected_connection=connection
            )
        )

    async def publish(
        self, project: UUID, device: UUID, request: ScriptPolicyPublish
    ) -> ScriptPolicyRead:
        async with tcp_connection_manager.lifecycle_lock(str(device)):
            await self._require_device(project, device, lock=True)
            row = await self._row(project, device)
            if (
                row.revision if row else 0
            ) != request.expected_policy_revision:
                raise HTTPException(409, "Device script policy changed")
            await self._no_other_mode(device)
            connection = self._connection(project, device)
            if (
                connection is None
                or connection.connection_instance_id
                != request.connection_instance_id
            ):
                raise HTTPException(
                    409, "Current local script connection is required"
                )
            status = await script_status(connection)
            if (
                status.state not in {"active", "awaiting_policy"}
                or status.session_id != request.session_id
            ):
                raise HTTPException(409, "Local script session changed")
            item = await reviewed_script(connection, request.script_id)
            scope = make_script_policy(
                request.expected_policy_revision + 1,
                script_id=request.script_id,
                grant_digest=request.grant_digest,
                timeout_seconds=request.timeout_seconds,
                max_output_bytes=request.max_output_bytes,
                max_runs=request.max_runs,
            )
            if not validate_script_scope(item, scope):
                raise HTTPException(
                    422, "Script policy exceeds current local review"
                )
            if row is None:
                row = DeviceScriptPolicy(device_id=device, project_id=project)
                self.db.add(row)
            for name in (
                "revision",
                "script_id",
                "grant_digest",
                "timeout_seconds",
                "max_output_bytes",
                "max_runs",
                "digest",
            ):
                setattr(row, name, getattr(scope, name))
            row.enabled, row.revoked_at = True, None
            clear_script_confirmation(connection, required=True)
            try:
                await self.db.commit()
            except BaseException as error:
                await self.db.rollback()
                await self._close(connection)
                if isinstance(error, IntegrityError):
                    raise HTTPException(
                        409, "Device script policy changed"
                    ) from None
                raise
            try:
                await self._require_device(project, device, lock=True)
                saved = await self._row(project, device)
                if (
                    saved is None
                    or not saved.enabled
                    or saved.digest != scope.digest
                ):
                    raise HTTPException(409, "Device script policy changed")
                await self._no_other_mode(device)
                clear_read_confirmation(connection)
                connection.desired_screen_policy = None
                connection.applied_screen_policy_digest = None
                connection.screen_grant = None
                if not await apply_script_policy(connection, scope):
                    await self._close(connection)
                await self.db.commit()
                await self.db.refresh(saved)
            except BaseException:
                await self._close(connection)
                raise
            return self._response(saved)

    async def revoke(
        self, project: UUID, device: UUID, request: ScriptPolicyRevoke
    ) -> ScriptPolicyRead:
        async with tcp_connection_manager.lifecycle_lock(str(device)):
            await self._require_device(project, device, lock=True)
            row = await self._row(project, device)
            if row is None:
                raise HTTPException(404, "Device script policy not found")
            if row.revision != request.expected_policy_revision:
                raise HTTPException(409, "Device script policy changed")
            connection = self._connection(project, device)
            bound = bool(connection and connection.script_policy_required)
            try:
                if row.enabled:
                    row.revision += 1
                    row.enabled = False
                    row.revoked_at = datetime.now(timezone.utc)
                    row.digest = make_script_policy(
                        row.revision,
                        script_id=row.script_id,
                        grant_digest=row.grant_digest,
                        timeout_seconds=row.timeout_seconds,
                        max_output_bytes=row.max_output_bytes,
                        max_runs=row.max_runs,
                    ).digest
                    await self.db.commit()
                    await self.db.refresh(row)
            finally:
                if bound and connection:
                    await self._close(connection)
            return self._response(row)
