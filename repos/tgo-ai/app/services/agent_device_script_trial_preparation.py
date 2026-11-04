"""Prepare and commit immutable script trials before any dispatch."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import ValidationError
from anyio import CancelScope
from sqlalchemy import select

from app.config import settings
from app.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError as RequestError,
)
from app.models.agent_device_script_trial import AgentDeviceScriptTrial
from app.schemas.agent_device_script_trials import (
    ScriptTrialStart,
    ScriptTrialReport,
    ScriptTrialReference,
)
from app.schemas.agent_device_script_requests import PreparationError
from app.schemas.device_script_execution import (
    PreparedScriptCall,
    validate_reviewed_script_configuration,
    validate_script_values,
)
from app.schemas.device_session import DeviceExecutionIdentity
from app.services.agent_activation import lock_account_agents
from app.services.agent_trial_fingerprint import private_digest
from app.services.agent_device_script_trial_integrity import (
    trial_reference_digest,
)
from app.services.device_script_preparation import prepare_script_call
from app.services.agent_device_script_requests import ScriptTrialRequests

if TYPE_CHECKING:
    from app.services.agent_device_script_trials import (
        AgentDeviceScriptTrialService,
    )


class ScriptTrialPreparation:
    def __init__(self, trials: "AgentDeviceScriptTrialService") -> None:
        self.trials, self.db = trials, trials.db
        self.bindings, self.device = trials.bindings, trials.device

    async def prepare(
        self, project: UUID, actor: UUID, employee: UUID,
        request: ScriptTrialStart, *, evidence_id: UUID | None = None,
    ) -> tuple[ScriptTrialReport, PreparedScriptCall | None]:
        request = ScriptTrialStart.model_validate_json(
            request.model_dump_json()
        )
        requests = ScriptTrialRequests(self.trials)
        row, created = await requests.register(
            project, actor, employee, request, evidence_id=evidence_id
        )
        if not created:
            state = await requests.state(
                project, actor, employee, request.request_id, refresh=False
            )
            if state.report is not None:
                return state.report, None
            raise ConflictError("原请求正在准备或已终止，请查询原请求")
        remaining = max(0, (
            row.deadline_at - datetime.now(timezone.utc)
        ).total_seconds())
        try:
            async with asyncio.timeout(remaining):
                return await self._prepare(project, actor, employee, request)
        except BaseException as error:
            category: PreparationError = (
                "preparation_timeout" if isinstance(error, TimeoutError)
                else "interrupted"
                if isinstance(error, asyncio.CancelledError)
                else "preparation_failed"
            )
            with CancelScope(shield=True):
                await self.db.rollback()
                await requests.fail(
                    project, actor, request.request_id, category
                )
            if isinstance(error, TimeoutError):
                raise ConflictError("试验准备已到期，请查询原请求") from None
            raise

    async def _prepare(
        self,
        project: UUID,
        actor: UUID,
        employee: UUID,
        request: ScriptTrialStart,
    ) -> tuple[ScriptTrialReport, PreparedScriptCall | None]:
        request = ScriptTrialStart.model_validate_json(
            request.model_dump_json()
        )
        digest = private_digest(
            {
                "agent_id": str(employee),
                "command": request.model_dump(mode="json"),
            }
        )
        try:
            await self.bindings._actor(project, actor)
            await self.bindings._agent(project, employee)
            await lock_account_agents(self.db, project)
            try:
                old = await self.trials._row(
                    project, actor, employee, request=request.request_id
                )
            except NotFoundError:
                old = None
            if old is not None:
                if old.command_digest != digest:
                    raise ConflictError("原试验请求内容不一致")
                await ScriptTrialRequests(self.trials).attach(
                    project, actor, employee, request, old.id
                )
                await self.db.commit()
                return await self.trials._view(old), None
            # A request key is unique for the actor across all employees.
            collision = await self.db.scalar(
                select(AgentDeviceScriptTrial.id).where(
                    AgentDeviceScriptTrial.project_id == project,
                    AgentDeviceScriptTrial.actor_id == actor,
                    AgentDeviceScriptTrial.request_id == request.request_id,
                )
            )
            if collision is not None:
                raise ConflictError("原请求编号已用于其他员工")
            binding = await self.trials._binding(
                project,
                actor,
                employee,
                request.binding_id,
                request.binding_revision,
            )
            device, policy, grant = (
                binding.device_id,
                binding.policy_digest,
                binding.grant_digest,
            )
            await self.db.commit()
            bound, item = await self.device.validate_script_configuration(
                project, device
            )
            bound, item = validate_reviewed_script_configuration(
                project, device, bound, item
            )
            if bound.policy.digest != policy or item.grant_digest != grant:
                raise ConflictError("设备策略已变化")
            budget = await self.trials._budget(binding)
            if budget.remaining_runs < 1:
                raise ConflictError("脚本剩余次数不足，测试也会消耗次数")
            expected = validate_script_values(
                item.result_fields, request.expected_fields, input=False
            )
            identity = DeviceExecutionIdentity(
                project_id=project,
                device_id=device,
                session_id=uuid4(),
                agent_id=employee,
                agent_name="脚本业务试验",
            )
            call = prepare_script_call(
                identity, bound, item, request.parameters, uuid4()
            )
            ref = ScriptTrialReference(
                identity=identity,
                execution_id=call.execution_id,
                policy_digest=call.policy_digest,
                parameters_digest=call.parameters_digest,
                context_digest=call.context_digest,
                timeout_seconds=call.timeout_seconds,
                max_output_bytes=call.max_output_bytes,
                descriptor=item,
                before_budget=budget,
            )
            encoded = ref.model_dump_json()
            if len(encoded.encode()) > 131072:
                raise ConflictError("审核元数据过大，无法建立试验")
            await lock_account_agents(self.db, project)
            await self.trials._binding(
                project,
                actor,
                employee,
                request.binding_id,
                request.binding_revision,
            )
            try:
                old = await self.trials._row(
                    project, actor, employee, request=request.request_id
                )
            except NotFoundError:
                old = None
            if old is not None:
                if old.command_digest != digest:
                    raise ConflictError("原试验请求内容不一致")
                await ScriptTrialRequests(self.trials).attach(
                    project, actor, employee, request, old.id
                )
                await self.db.commit()
                return await self.trials._view(old), None
            collision = await self.db.scalar(
                select(AgentDeviceScriptTrial.id).where(
                    AgentDeviceScriptTrial.project_id == project,
                    AgentDeviceScriptTrial.actor_id == actor,
                    AgentDeviceScriptTrial.request_id == request.request_id,
                )
            )
            if collision is not None:
                raise ConflictError("原请求编号已用于其他员工")
            now = datetime.now(timezone.utc)
            running = await self.db.scalar(
                select(AgentDeviceScriptTrial.id)
                .where(
                    AgentDeviceScriptTrial.project_id == project,
                    AgentDeviceScriptTrial.agent_id == employee,
                    AgentDeviceScriptTrial.status == "unknown",
                    AgentDeviceScriptTrial.deleted_at.is_(None),
                    AgentDeviceScriptTrial.dispatch_deadline_at > now,
                )
                .limit(1)
            )
            if running is not None:
                raise ConflictError("员工已有未确认的脚本试验，请查询原编号")
            result = AgentDeviceScriptTrial(
                id=uuid4(),
                project_id=project,
                actor_id=actor,
                agent_id=employee,
                binding_id=request.binding_id,
                binding_revision=request.binding_revision,
                request_id=request.request_id,
                device_id=device,
                execution_id=call.execution_id,
                command_digest=digest,
                expected_digest=private_digest(dict(expected)),
                policy_digest=policy,
                grant_digest=grant,
                reference_json=encoded,
                reference_digest="",
                status="unknown",
                error_code="result_unknown",
                created_at=now,
                dispatch_claimed_at=now,
                dispatch_deadline_at=now
                + timedelta(seconds=call.timeout_seconds + 30),
                expires_at=now
                + timedelta(hours=settings.agent_trial_review_validity_hours),
                remaining_runs=budget.remaining_runs,
            )
            result.reference_digest = trial_reference_digest(result, ref)
            self.db.add(result)
            await self.db.flush()
            await ScriptTrialRequests(self.trials).attach(
                project, actor, employee, request, result.id
            )
            await self.db.commit()
            return await self.trials._view(result), call
        except (ValidationError, ValueError, TypeError):
            await self.db.rollback()
            raise RequestError("请按已审核字段填写测试输入和预期结果") from None
        except BaseException:
            await self.db.rollback()
            raise
