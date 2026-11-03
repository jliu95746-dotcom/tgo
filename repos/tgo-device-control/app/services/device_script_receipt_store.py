"""Read and finalize receipts without persisting their business bodies."""

import hashlib
from datetime import datetime, timezone
from typing import cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device, DeviceSessionStep
from app.models.device_script_execution import DeviceScriptExecution
from app.models.device_script_policy import DeviceScriptPolicy
from app.schemas.device_access import DeviceServicePrincipal
from app.schemas.device_script_execution import (
    BusinessValue,
    ScriptWireReceipt,
    canonical_script_values,
    validate_script_values,
)
from app.schemas.device_script_policy import ScriptDescriptor
from app.schemas.device_script_receipt import (
    ScriptExecutionRead,
    StoredScriptOutcome,
)
from app.services.device_script_connection import validate_script_scope
from app.services.device_script_execution_store import (
    require_script_execution_scope,
    script_execution_context_digest,
)
from app.services.device_script_publication import stored_script_policy
from app.services.device_sessions import (
    DeviceSessionService,
    effective_session_status,
)


class ScriptReceiptStore:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def owned(
        self,
        principal: DeviceServicePrincipal,
        device: UUID,
        execution: UUID,
        *,
        lock: bool = False,
    ) -> DeviceScriptExecution:
        session = require_script_execution_scope(principal, device)
        query = (
            select(DeviceScriptExecution)
            .join(Device)
            .where(
                DeviceScriptExecution.device_id == device,
                DeviceScriptExecution.execution_id == execution,
                DeviceScriptExecution.project_id == principal.project_id,
                DeviceScriptExecution.session_id == session,
                Device.project_id == principal.project_id,
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update(of=DeviceScriptExecution)
        row = await self.db.scalar(query)
        if row is None:
            raise HTTPException(404, "Script execution not found")
        if row.policy_digest != principal.script_policy_digest:
            raise HTTPException(409, "Script execution identity conflict")
        return row

    @staticmethod
    def metadata(row: DeviceScriptExecution) -> ScriptExecutionRead:
        return ScriptExecutionRead(
            device_id=row.device_id,
            execution_id=row.execution_id,
            session_id=row.session_id,
            agent_id=row.agent_id,
            workflow_execution_id=row.workflow_execution_id,
            policy_digest=row.policy_digest,
            parameters_digest=row.parameters_digest,
            context_digest=row.context_digest,
            status=cast(StoredScriptOutcome, row.status),
            failure=row.failure,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )

    async def active(
        self,
        principal: DeviceServicePrincipal,
        row: DeviceScriptExecution,
        *,
        lock: bool = False,
    ) -> bool:
        try:
            session, _ = await DeviceSessionService(self.db)._owned(
                principal.project_id,
                row.session_id,
                row.device_id,
                lock=lock,
            )
        except HTTPException:
            return False
        policy = await self.db.get(
            DeviceScriptPolicy, row.device_id, populate_existing=True
        )
        return bool(
            effective_session_status(session, datetime.now(timezone.utc))
            == "running"
            and session.agent_id == row.agent_id
            and policy
            and policy.enabled
            and policy.project_id == principal.project_id
            and policy.digest == row.policy_digest
            and script_execution_context_digest(
                principal.project_id, row.device_id, session, row.policy_digest
            )
            == row.context_digest
        )

    async def finalize(
        self,
        principal: DeviceServicePrincipal,
        device: UUID,
        execution: UUID,
        wire: ScriptWireReceipt,
        item: ScriptDescriptor | None,
    ) -> ScriptExecutionRead:
        # Same device-first lock order as reservation and policy publication.
        await self.db.scalar(
            select(Device.id)
            .where(
                Device.id == device,
                Device.project_id == principal.project_id,
            )
            .with_for_update()
        )
        row = await self.owned(principal, device, execution, lock=True)
        if (
            wire.execution_id != str(execution)
            or wire.policy_digest != row.policy_digest
            or wire.parameters_digest != row.parameters_digest
            or wire.context_digest != row.context_digest
        ):
            raise HTTPException(409, "Device receipt identity mismatch")
        active = await self.active(principal, row, lock=True)
        fields: dict[str, BusinessValue] = {}
        result_digest = None
        if active and wire.status == "succeeded":
            policy = await self.db.get(
                DeviceScriptPolicy, device, populate_existing=True
            )
            if (
                policy is None
                or item is None
                or not validate_script_scope(
                    item, stored_script_policy(policy)
                )
            ):
                raise HTTPException(409, "Reviewed script scope changed")
            try:
                fields = validate_script_values(
                    item.result_fields, wire.fields, input=False
                )
                data = canonical_script_values(fields)
                if len(data) > policy.max_output_bytes:
                    raise ValueError(
                        "Receipt output exceeds the reviewed budget"
                    )
            except ValueError as error:
                raise HTTPException(502, str(error)) from error
            result_digest = hashlib.sha256(data).hexdigest()
        if row.status != "unknown":
            # A later incomplete/conflicting reply cannot overwrite a terminal
            # observation or replace its result hash.
            expose = bool(
                active
                and row.status == "succeeded"
                and wire.status == row.status
                and row.result_digest == result_digest
            )
        elif not active or wire.status not in {"unknown", "not_found"}:
            row.status = wire.status if active else "canceled"
            row.failure = (
                wire.failure if active else "execution_scope_inactive"
            )
            row.result_digest = result_digest
            row.finished_at = datetime.now(timezone.utc)
            step = await self.db.get(
                DeviceSessionStep, row.step_id, populate_existing=True
            )
            if step is not None and step.status == "running":
                step.status = (
                    "completed" if row.status == "succeeded" else "failed"
                )
                step.ended_at = row.finished_at
                if row.status != "succeeded":
                    session, _ = await DeviceSessionService(self.db)._owned(
                        principal.project_id, row.session_id, device
                    )
                    session.failed_actions_count += 1
            expose = row.status == "succeeded"
        else:
            expose = False
        response = self.metadata(row)
        if expose:
            response = response.model_copy(
                update={"result_available": True, "fields": fields}
            )
        await self.db.commit()
        return response
