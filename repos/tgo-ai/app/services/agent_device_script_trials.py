"""Durable script trials; original requests only query their receipts."""

import asyncio
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
)
from app.models.agent_device_script_trial import AgentDeviceScriptTrial
from app.models.agent_device_script_binding import AgentDeviceScriptBinding
from app.schemas.agent_device_script_trials import (
    ScriptTrialStart,
    ScriptTrialReport,
    ScriptTrialReference,
    TrialError,
    TrialStatus,
)
from app.schemas.device_script_budget import ScriptBudgetRead
from app.schemas.device_script_execution import (
    PreparedScriptCall,
    ScriptReceiptReference,
    ScriptExecutionRead,
    validate_reviewed_script_configuration,
    validate_script_receipt,
)
from app.services.agent_activation import lock_account_agents
from app.services.agent_device_script_bindings import (
    AgentDeviceScriptBindingService,
    ConfigurationActorOwner,
    ScriptConfigurationOwner,
)
from app.services.agent_trial_fingerprint import private_digest
from app.services.agent_device_script_trial_integrity import (
    trial_reference_digest,
)
from app.services.agent_device_script_requests import ScriptTrialRequests
from app.services.api_service import api_service_client
from app.services.device_control_client import device_control_client
from app.services.device_execution import (
    DeviceSessionClient,
    track_device_identity,
)


class ScriptTrialDevice(
    ScriptConfigurationOwner, DeviceSessionClient, Protocol
):
    async def read_script_budget(
        self,
        project: UUID,
        device: UUID,
        policy_digest: str,
        grant_digest: str,
    ) -> ScriptBudgetRead:
        ...

    async def execute_script(
        self, call: PreparedScriptCall
    ) -> ScriptExecutionRead:
        ...

    async def read_script(
        self, call: ScriptReceiptReference
    ) -> ScriptExecutionRead:
        ...


class AgentDeviceScriptTrialService:
    def __init__(
        self,
        db: AsyncSession,
        actor_owner: ConfigurationActorOwner = api_service_client,
        device: ScriptTrialDevice = device_control_client,
    ) -> None:
        self.db, self.device = db, device
        self.bindings = AgentDeviceScriptBindingService(
            db, actor_owner, device
        )

    async def _row(
        self,
        project: UUID,
        actor: UUID,
        employee: UUID,
        *,
        report: UUID | None = None,
        request: UUID | None = None,
    ) -> AgentDeviceScriptTrial:
        query = select(AgentDeviceScriptTrial).where(
            AgentDeviceScriptTrial.project_id == project,
            AgentDeviceScriptTrial.actor_id == actor,
            AgentDeviceScriptTrial.agent_id == employee,
            AgentDeviceScriptTrial.deleted_at.is_(None),
        )
        query = (
            query.where(AgentDeviceScriptTrial.id == report)
            if report
            else query.where(AgentDeviceScriptTrial.request_id == request)
        )
        row: AgentDeviceScriptTrial | None = await self.db.scalar(
            query.execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("员工脚本试验")
        return row

    async def _binding(
        self,
        project: UUID,
        actor: UUID,
        employee: UUID,
        binding_id: UUID,
        revision: int,
        *,
        require_paused: bool = True,
    ) -> AgentDeviceScriptBinding:
        await self.bindings._actor(project, actor)
        agent = await self.bindings._agent(project, employee)
        binding = await self.bindings._binding(project, employee)
        if (
            require_paused
            and agent.is_active
            or binding is None
            or not binding.enabled
            or binding.id != binding_id
            or binding.revision != revision
            or agent.bound_device_id
            or agent.bound_device_policy_digest
        ):
            raise ConflictError("员工脚本配置已变化，请刷新后重新测试")
        return binding

    async def _budget(
        self, binding: AgentDeviceScriptBinding
    ) -> ScriptBudgetRead:
        raw = await self.device.read_script_budget(
            binding.project_id,
            binding.device_id,
            binding.policy_digest,
            binding.grant_digest,
        )
        budget = ScriptBudgetRead.model_validate_json(raw.model_dump_json())
        age = (datetime.now(timezone.utc) - budget.observed_at).total_seconds()
        if (
            budget.project_id != binding.project_id
            or budget.device_id != binding.device_id
            or budget.policy_digest != binding.policy_digest
            or budget.grant_digest != binding.grant_digest
            or not -5 <= age <= 30
        ):
            raise ConflictError("脚本剩余次数的范围或时间已经变化")
        return budget

    @staticmethod
    def _reference(row: AgentDeviceScriptTrial) -> ScriptTrialReference:
        try:
            ref = ScriptTrialReference.model_validate_json(row.reference_json)
            if (
                trial_reference_digest(row, ref) != row.reference_digest
                or ref.identity.project_id != row.project_id
                or ref.identity.device_id != row.device_id
                or ref.identity.agent_id != row.agent_id
                or ref.execution_id != row.execution_id
                or ref.policy_digest != row.policy_digest
                or ref.descriptor.grant_digest != row.grant_digest
            ):
                raise ValueError("Stored trial scope changed")
            return ref
        except (ValueError, ValidationError):
            raise ConflictError("原试验执行身份无法核验") from None

    async def _current(self, row: AgentDeviceScriptTrial) -> ScriptBudgetRead:
        original = self._reference(row)
        binding = await self._binding(
            row.project_id,
            row.actor_id,
            row.agent_id,
            row.binding_id,
            row.binding_revision,
            require_paused=row.status == "unknown",
        )
        bound, item = await self.device.validate_script_configuration(
            row.project_id, row.device_id
        )
        bound, item = validate_reviewed_script_configuration(
            row.project_id, row.device_id, bound, item
        )
        if (
            bound.policy.digest != row.policy_digest
            or item.grant_digest != row.grant_digest
            or item.model_dump_json() != original.descriptor.model_dump_json()
        ):
            raise ConflictError("设备脚本审核或策略已变化")
        budget = await self._budget(binding)
        before = original.before_budget
        if (
            budget.connection_instance_id != before.connection_instance_id
            or budget.session_id != before.session_id
        ):
            raise ConflictError("设备连接已变化，请重新测试")
        await self._binding(
            row.project_id,
            row.actor_id,
            row.agent_id,
            row.binding_id,
            row.binding_revision,
            require_paused=row.status == "unknown",
        )
        return budget

    async def _view(self, row: AgentDeviceScriptTrial) -> ScriptTrialReport:
        applicability = "current"
        if row.expires_at <= datetime.now(timezone.utc):
            applicability = "expired"
        else:
            try:
                await self._current(row)
            except (ConflictError, NotFoundError):
                applicability = "changed"
            except (ExternalServiceError, ValueError, ValidationError):
                applicability = "unknown"
        values = {
            name: getattr(row, name)
            for name in ScriptTrialReport.model_fields
            if name
            not in {"scope", "can_enable", "assessment", "applicability"}
        }
        return ScriptTrialReport.model_validate(
            {**values, "applicability": applicability}
        )

    async def prepare(
        self,
        project: UUID,
        actor: UUID,
        employee: UUID,
        request: ScriptTrialStart, *, evidence_id: UUID | None = None,
    ) -> tuple[ScriptTrialReport, PreparedScriptCall | None]:
        from app.services.agent_device_script_trial_preparation import (
            ScriptTrialPreparation,
        )

        return await ScriptTrialPreparation(self).prepare(
            project, actor, employee, request, evidence_id=evidence_id
        )

    async def _set(
        self,
        row: AgentDeviceScriptTrial,
        status: TrialStatus,
        error: TrialError | None,
        result_digest: str | None = None,
        remaining: int | None = None,
    ) -> None:
        await lock_account_agents(self.db, row.project_id)
        await self.db.execute(
            update(AgentDeviceScriptTrial)
            .where(
                AgentDeviceScriptTrial.id == row.id,
                AgentDeviceScriptTrial.status == "unknown",
            )
            .values(
                status=status,
                error_code=error,
                result_digest=result_digest,
                remaining_runs=remaining,
                completed_at=datetime.now(timezone.utc)
                if status != "unknown"
                else None,
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()

    async def _consume(
        self, row: AgentDeviceScriptTrial, raw: ScriptExecutionRead
    ) -> None:
        try:
            receipt = validate_script_receipt(
                self._reference(row).query(), raw
            )
            await ScriptTrialRequests(self).observe(row, receipt)
            from app.services.agent_device_script_evidence import (
                capture_case_result,
            )
            try:
                await capture_case_result(self, row, receipt)
            except ConflictError:
                await self._set(row, "failed", "result_unavailable")
                return
            await lock_account_agents(self.db, row.project_id)
            budget = await self._current(row)
        except ConflictError:
            await self._set(row, "failed", "configuration_changed")
            return
        except (ValueError, ValidationError):
            await self._set(row, "failed", "invalid_receipt")
            return
        if receipt.status == "unknown":
            await self._set(
                row,
                "unknown",
                "result_unknown",
                remaining=budget.remaining_runs,
            )
        elif receipt.status == "canceled":
            await self._set(
                row, "failed", "interrupted",
                remaining=budget.remaining_runs,
            )
        elif receipt.status != "succeeded":
            await self._set(
                row,
                "failed",
                "execution_failed",
                remaining=budget.remaining_runs,
            )
        elif not receipt.result_available:
            await self._set(
                row,
                "failed",
                "result_unavailable",
                remaining=budget.remaining_runs,
            )
        else:
            fields: dict[str, JsonValue] = dict(receipt.fields)
            digest = private_digest(fields)
            await self._set(
                row,
                "matched" if digest == row.expected_digest else "mismatched",
                None,
                digest,
                budget.remaining_runs,
            )

    async def start(
        self,
        project: UUID,
        actor: UUID,
        employee: UUID,
        request: ScriptTrialStart, *, evidence_id: UUID | None = None,
    ) -> ScriptTrialReport:
        report, call = await self.prepare(
            project, actor, employee, request, evidence_id=evidence_id
        )
        if call is None:
            return report
        row = await self._row(project, actor, employee, report=report.id)
        try:
            async with track_device_identity(
                call.identity, self.device
            ) as execution:
                budget = await self._current(row)
                if budget.remaining_runs < 1:
                    raise ConflictError("剩余次数不足")
                claimed = await ScriptTrialRequests(self).claim(row)
                if not claimed:
                    await self._set(row, "failed", "interrupted")
                else:
                    receipt = await self.device.execute_script(call)
                    await self._consume(row, receipt)
                    current = await self._row(
                        project, actor, employee, report=row.id
                    )
                    execution.complete(current.status == "matched")
        except ConflictError:
            await self.db.rollback()
            await self._set(row, "failed", "configuration_changed")
        except ExternalServiceError:
            await self.db.rollback()
            await self._set(row, "unknown", "result_unknown")
        except asyncio.CancelledError:
            await self.db.rollback()
            raise
        await self.bindings._actor(project, actor)
        return await self._view(
            await self._row(project, actor, employee, report=report.id)
        )

    async def get(
        self, project: UUID, actor: UUID, employee: UUID, report: UUID
    ) -> ScriptTrialReport:
        await self.bindings._actor(project, actor)
        await self.bindings._agent(project, employee)
        row = await self._row(project, actor, employee, report=report)
        if row.status == "unknown" and row.expires_at > datetime.now(
            timezone.utc
        ):
            try:
                await self._current(row)
                await self.db.commit()
                raw = await self.device.read_script(
                    self._reference(row).query()
                )
                await self._consume(row, raw)
            except ConflictError:
                await self.db.rollback()
                await self._set(row, "failed", "configuration_changed")
            except ExternalServiceError:
                await self.db.rollback()
        return await self._view(
            await self._row(project, actor, employee, report=report)
        )

    async def by_request(
        self, project: UUID, actor: UUID, employee: UUID, request: UUID
    ) -> ScriptTrialReport:
        await self.bindings._actor(project, actor)
        row = await self._row(project, actor, employee, request=request)
        return await self.get(project, actor, employee, row.id)
