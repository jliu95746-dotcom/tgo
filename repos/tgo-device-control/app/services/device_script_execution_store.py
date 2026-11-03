"""Reserve one immutable dispatch and monitoring step before device I/O."""

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device, DeviceSession, DeviceSessionStep
from app.models.device_script_execution import DeviceScriptExecution
from app.models.device_script_policy import DeviceScriptPolicy
from app.schemas.device_access import DeviceServicePrincipal
from app.schemas.device_script_execution import (
    BusinessValue,
    script_input_digest,
    validate_script_values,
)
from app.schemas.device_script_policy import ScriptDescriptor
from app.services.device_script_connection import validate_script_scope
from app.services.device_script_publication import (
    DeviceScriptPublicationService,
    stored_script_policy,
)
from app.services.device_sessions import DeviceSessionService


def script_execution_context_digest(
    project: UUID,
    device: UUID,
    session: DeviceSession,
    policy_digest: str,
) -> str:
    payload = (
        f"tgo-approved-script-context-v1\n{project}\n{device}\n{session.id}\n"
        f"{session.agent_id}\n{session.workflow_execution_id or ''}\n"
        f"{policy_digest}\n"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_script_execution_scope(
    principal: DeviceServicePrincipal,
    device: UUID,
) -> UUID:
    if (
        principal.sub != "tgo-ai"
        or principal.device_id != device
        or principal.session_id is None
        or principal.script_policy_digest is None
    ):
        raise HTTPException(
            403, "Signed AI script execution scope is required"
        )
    return principal.session_id


class ScriptExecutionStore:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def reserve(
        self,
        principal: DeviceServicePrincipal,
        device: UUID,
        execution: UUID,
        parameters: dict[str, BusinessValue],
        item: ScriptDescriptor | None,
    ) -> tuple[DeviceScriptExecution, bool]:
        session_id = require_script_execution_scope(principal, device)
        project = principal.project_id
        owned = await self.db.scalar(
            select(Device.id)
            .where(
                Device.id == device,
                Device.project_id == project,
            )
            .with_for_update()
        )
        if owned is None:
            raise HTTPException(404, "Device not found")
        session, _ = await DeviceSessionService(self.db)._owned(
            project, session_id, device, lock=True
        )
        if session.agent_id is None:
            raise HTTPException(409, "Device session has no bound employee")
        old = await self.db.get(
            DeviceScriptExecution, (device, execution), populate_existing=True
        )
        if old is not None:
            digest = script_input_digest(old.grant_digest, parameters)
            context = script_execution_context_digest(
                project, device, session, old.policy_digest
            )
            if (
                old.project_id != project
                or old.session_id != session_id
                or old.agent_id != session.agent_id
                or old.policy_digest != principal.script_policy_digest
                or old.parameters_digest != digest
                or old.context_digest != context
            ):
                raise HTTPException(409, "Script execution identity conflict")
            await self.db.commit()
            return old, False
        DeviceSessionService._require_active(session)
        policy = await self.db.scalar(
            select(DeviceScriptPolicy)
            .where(
                DeviceScriptPolicy.device_id == device,
                DeviceScriptPolicy.project_id == project,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if (
            policy is None
            or not policy.enabled
            or policy.digest != principal.script_policy_digest
        ):
            raise HTTPException(409, "Bound script policy is not active")
        await DeviceScriptPublicationService(self.db)._no_other_mode(device)
        if item is None or not validate_script_scope(
            item, stored_script_policy(policy)
        ):
            raise HTTPException(409, "Current reviewed script scope changed")
        try:
            values = validate_script_values(item.parameters, parameters)
            parameters_digest = script_input_digest(
                policy.grant_digest, values
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        policy_count = await self.db.scalar(
            select(func.count())
            .select_from(DeviceScriptExecution)
            .where(
                DeviceScriptExecution.device_id == device,
                DeviceScriptExecution.policy_digest == policy.digest,
            )
        )
        grant_count = await self.db.scalar(
            select(func.count())
            .select_from(DeviceScriptExecution)
            .where(
                DeviceScriptExecution.device_id == device,
                DeviceScriptExecution.grant_digest == policy.grant_digest,
            )
        )
        if (policy_count or 0) >= policy.max_runs or (
            grant_count or 0
        ) >= item.max_runs:
            raise HTTPException(409, "Script execution budget is exhausted")
        now, step = datetime.now(timezone.utc), uuid4()
        self.db.add(
            DeviceSessionStep(
                id=step,
                session_id=session_id,
                tool_name="approved_script:" + policy.script_id,
                status="running",
                started_at=now,
            )
        )
        session.actions_count += 1
        # Both flushes belong to the same commit; a receipt failure rolls back
        # its step and counter too. No request body is added to either table.
        await self.db.flush()
        receipt = DeviceScriptExecution(
            device_id=device,
            execution_id=execution,
            project_id=project,
            session_id=session_id,
            step_id=step,
            agent_id=session.agent_id,
            workflow_execution_id=session.workflow_execution_id,
            script_id=policy.script_id,
            policy_revision=policy.revision,
            policy_digest=policy.digest,
            grant_digest=policy.grant_digest,
            parameters_digest=parameters_digest,
            context_digest=script_execution_context_digest(
                project, device, session, policy.digest
            ),
            status="unknown",
            failure="dispatch_unconfirmed",
            created_at=now,
        )
        self.db.add(receipt)
        try:
            await self.db.commit()
        except BaseException as error:
            await self.db.rollback()
            if isinstance(error, IntegrityError):
                raise HTTPException(
                    409, "Script execution identity changed"
                ) from None
            raise
        return receipt, True
