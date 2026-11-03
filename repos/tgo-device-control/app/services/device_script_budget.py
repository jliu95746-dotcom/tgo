"""Observe server reservations and the authenticated device journal."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_script_execution import DeviceScriptExecution
from app.schemas.device_script_budget import ScriptBudgetRead, ScriptBudgetWire
from app.schemas.device_script_publication import ScriptPolicyRead
from app.services.device_script_connection import (
    connection_is_current,
    reviewed_script,
    validate_script_scope,
)
from app.services.device_script_publication import (
    DeviceScriptPublicationService,
)
from app.services.tcp_connection_manager import tcp_connection_manager


class DeviceScriptBudgetService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.publication = DeviceScriptPublicationService(db)

    async def _current(self, project: UUID, device: UUID) -> ScriptPolicyRead:
        await self.publication._require_device(project, device)
        row = await self.publication._row(project, device)
        if row is None:
            raise HTTPException(409, "Device script policy is unavailable")
        await self.publication._no_other_mode(device)
        current = self.publication._response(row)
        if current.status != "active" or current.revoked_at is not None:
            raise HTTPException(409, "Device script policy is not confirmed")
        return current

    async def get(self, project: UUID, device: UUID) -> ScriptBudgetRead:
        async with tcp_connection_manager.lifecycle_lock(str(device)):
            before = await self._current(project, device)
            await self.db.commit()  # Release database locks before device I/O.
            connection = self.publication._connection(project, device)
            if connection is None:
                raise HTTPException(409, "Device connection changed")
            instance, session = (
                connection.connection_instance_id,
                connection.script_session_id,
            )
            if session is None:
                raise HTTPException(409, "Device script session changed")
            item = await reviewed_script(connection, before.policy.script_id)
            if not validate_script_scope(item, before.policy):
                raise HTTPException(409, "Current reviewed script changed")
            raw = await connection.send_request(
                "scripts/budget/get",
                dict(
                    protocol_version=1,
                    session_id=session,
                    policy_digest=before.policy.digest,
                ),
                timeout=10,
            )
            try:
                wire = ScriptBudgetWire.model_validate(raw)
            except (ValidationError, ValueError, TypeError):
                raise HTTPException(
                    502, "Device script budget is unavailable"
                ) from None
            after = await self._current(project, device)
            if (
                not connection_is_current(connection)
                or connection.connection_instance_id != instance
                or connection.script_session_id != session
                or after.policy != before.policy
                or wire.session_id != session
                or wire.policy_digest != before.policy.digest
                or wire.grant_digest != item.grant_digest
                or wire.policy_max_runs != before.policy.max_runs
                or wire.grant_max_runs != item.max_runs
            ):
                raise HTTPException(409, "Device script budget scope changed")
            counts: list[int] = []
            for column, digest in (
                (DeviceScriptExecution.policy_digest, before.policy.digest),
                (DeviceScriptExecution.grant_digest, item.grant_digest),
            ):
                count = await self.db.scalar(
                    select(func.count())
                    .select_from(DeviceScriptExecution)
                    .where(
                        DeviceScriptExecution.project_id == project,
                        DeviceScriptExecution.device_id == device,
                        column == digest,
                    )
                )
                counts.append(count or 0)
            ack = connection.applied_script_policy_ack
            if (
                self.publication._connection(project, device) is not connection
                or connection.connection_instance_id != instance
                or connection.script_session_id != session
                or not connection.script_policy_required
                or connection.desired_script_policy != before.policy
                or connection.desired_read_policy_digest is not None
                or connection.desired_screen_policy is not None
                or ack is None
                or ack.session_id != session
                or ack.digest != before.policy.digest
                or ack.revision != before.policy.revision
            ):
                raise HTTPException(409, "Device script connection changed")
            try:
                return ScriptBudgetRead(
                    project_id=project,
                    device_id=device,
                    connection_instance_id=instance,
                    session_id=session,
                    policy_digest=before.policy.digest,
                    grant_digest=item.grant_digest,
                    policy_max_runs=wire.policy_max_runs,
                    grant_max_runs=wire.grant_max_runs,
                    server_policy_used=counts[0],
                    server_grant_used=counts[1],
                    device_policy_used=wire.policy_used,
                    device_grant_used=wire.grant_used,
                    remaining_runs=min(
                        wire.policy_max_runs - counts[0],
                        wire.policy_max_runs - wire.policy_used,
                        wire.grant_max_runs - counts[1],
                        wire.grant_max_runs - wire.grant_used,
                    ),
                    observed_at=datetime.now(timezone.utc),
                )
            except (ValidationError, ValueError, TypeError):
                raise HTTPException(
                    409, "Stored script budget cannot be verified"
                ) from None
