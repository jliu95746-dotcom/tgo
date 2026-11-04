"""Durable preparation and cancellation with original-execution fencing."""

import asyncio
import hmac
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import and_, or_, select, update

from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.models.agent_device_script_trial import AgentDeviceScriptTrial
from app.models.agent_device_script_trial_request import (
    AgentDeviceScriptTrialRequest,
)
from app.schemas.agent_device_script_requests import (
    PreparationError, ScriptTrialControl, ScriptTrialRequestState,
)
from app.schemas.agent_device_script_trials import ScriptTrialStart
from app.schemas.device_script_execution import (
    ScriptExecutionRead, validate_script_receipt,
)
from app.services.agent_activation import lock_account_agents
from app.services.agent_trial_fingerprint import private_digest

if TYPE_CHECKING:
    from app.services.agent_device_script_trials import (
        AgentDeviceScriptTrialService,
    )


class ScriptTrialRequests:
    def __init__(
        self, trials: "AgentDeviceScriptTrialService", *,
        preparation_seconds: float = 30, lock_seconds: float = 5,
    ) -> None:
        self.trials, self.db = trials, trials.db
        self.preparation_seconds, self.lock_seconds = (
            preparation_seconds, lock_seconds
        )

    async def _lock(self, project: UUID) -> None:
        async with asyncio.timeout(self.lock_seconds):
            await lock_account_agents(self.db, project)

    async def _find(
        self, project: UUID, actor: UUID, request: UUID
    ) -> AgentDeviceScriptTrialRequest | None:
        query = select(AgentDeviceScriptTrialRequest).where(
            AgentDeviceScriptTrialRequest.project_id == project,
            AgentDeviceScriptTrialRequest.actor_id == actor,
            AgentDeviceScriptTrialRequest.request_id == request,
        ).execution_options(populate_existing=True)
        row: AgentDeviceScriptTrialRequest | None = await self.db.scalar(query)
        return row

    @staticmethod
    def _scope(
        row: AgentDeviceScriptTrialRequest, employee: UUID,
        command: ScriptTrialControl,
    ) -> None:
        if (
            row.agent_id != employee or row.binding_id != command.binding_id
            or row.binding_revision != command.binding_revision
        ):
            raise ConflictError("原脚本试验请求范围不一致")

    async def register(
        self, project: UUID, actor: UUID, employee: UUID,
        command: ScriptTrialStart, *, evidence_id: UUID | None = None,
    ) -> tuple[AgentDeviceScriptTrialRequest, bool]:
        digest = private_digest({
            "agent_id": str(employee),
            "command": command.model_dump(mode="json"),
        })
        control = ScriptTrialControl.model_validate(command.model_dump(
            include=set(ScriptTrialControl.model_fields)
        ))
        try:
            async with asyncio.timeout(self.lock_seconds * 2):
                await self.trials.bindings._actor(project, actor)
                await self.trials.bindings._agent(project, employee)
                await self._lock(project)
                from app.services.agent_device_script_evidence import (
                    admit_case_request,
                )
                await admit_case_request(
                    self.db, project, actor, employee, command.request_id,
                    digest, evidence_id,
                )
                row = await self._find(project, actor, command.request_id)
                if row is not None:
                    self._scope(row, employee, control)
                    if row.command_digest is not None and not (
                        hmac.compare_digest(row.command_digest, digest)
                    ):
                        raise ConflictError("原脚本试验请求内容不一致")
                    await self.db.commit()
                    return row, False
                original = await self.db.scalar(select(
                    AgentDeviceScriptTrial
                ).where(
                    AgentDeviceScriptTrial.project_id == project,
                    AgentDeviceScriptTrial.actor_id == actor,
                    AgentDeviceScriptTrial.request_id == command.request_id,
                ))
                if original is not None:
                    if (
                        original.agent_id != employee
                        or original.command_digest != digest
                    ):
                        raise ConflictError("原脚本试验请求内容不一致")
                    row = self._legacy(original)
                    self.db.add(row)
                    await self.db.commit()
                    return row, False
                pending = await self.db.scalar(select(
                    AgentDeviceScriptTrialRequest.id
                ).outerjoin(
                    AgentDeviceScriptTrial,
                    AgentDeviceScriptTrialRequest.report_id
                    == AgentDeviceScriptTrial.id,
                ).where(
                    AgentDeviceScriptTrialRequest.project_id == project,
                    AgentDeviceScriptTrialRequest.agent_id == employee,
                    or_(
                        and_(
                            AgentDeviceScriptTrialRequest.status
                            == "preparing",
                            AgentDeviceScriptTrialRequest.deadline_at
                            > datetime.now(timezone.utc),
                        ),
                        and_(
                            AgentDeviceScriptTrialRequest.status == "ready",
                            AgentDeviceScriptTrialRequest.stop_status.in_(
                                ["none", "requested"]
                            ),
                            or_(
                                AgentDeviceScriptTrialRequest.dispatched_at
                                .is_not(None),
                                AgentDeviceScriptTrial.status == "unknown",
                            ),
                        ),
                    ),
                ).limit(1))
                if pending is not None:
                    raise ConflictError("员工仍有未确认的原请求，请查询或取消")
                await self.trials._binding(
                    project, actor, employee,
                    command.binding_id, command.binding_revision,
                )
                now = datetime.now(timezone.utc)
                row = AgentDeviceScriptTrialRequest(
                    id=uuid4(), project_id=project, actor_id=actor,
                    agent_id=employee, request_id=command.request_id,
                    binding_id=command.binding_id,
                    binding_revision=command.binding_revision,
                    command_digest=digest, status="preparing",
                    stop_status="none", created_at=now,
                    deadline_at=now + timedelta(
                        seconds=self.preparation_seconds
                    ),
                )
                self.db.add(row)
                await self.db.commit()
                return row, True
        except BaseException:
            await self.db.rollback()
            raise

    @staticmethod
    def _legacy(row: AgentDeviceScriptTrial) -> AgentDeviceScriptTrialRequest:
        ended = row.status in {"matched", "mismatched"} or row.error_code in {
            "execution_failed", "result_unavailable"
        }
        return AgentDeviceScriptTrialRequest(
            id=uuid4(), project_id=row.project_id, actor_id=row.actor_id,
            agent_id=row.agent_id, request_id=row.request_id,
            binding_id=row.binding_id, binding_revision=row.binding_revision,
            command_digest=row.command_digest, status="ready",
            stop_status="finished" if ended else "none", report_id=row.id,
            created_at=row.created_at, deadline_at=row.dispatch_deadline_at,
            completed_at=row.created_at,
            dispatched_at=row.dispatch_claimed_at,
        )

    async def fail(
        self, project: UUID, actor: UUID, request: UUID,
        error: PreparationError,
    ) -> None:
        async with asyncio.timeout(self.lock_seconds):
            await self.db.execute(update(AgentDeviceScriptTrialRequest).where(
                AgentDeviceScriptTrialRequest.project_id == project,
                AgentDeviceScriptTrialRequest.actor_id == actor,
                AgentDeviceScriptTrialRequest.request_id == request,
                AgentDeviceScriptTrialRequest.status == "preparing",
            ).values(
                status="failed", error_code=error,
                completed_at=datetime.now(timezone.utc),
            ).execution_options(synchronize_session=False))
            await self.db.commit()

    async def attach(
        self, project: UUID, actor: UUID, employee: UUID,
        command: ScriptTrialStart, report: UUID,
    ) -> None:
        row = await self._find(project, actor, command.request_id)
        if row is None:
            raise ConflictError("原试验准备记录缺失")
        self._scope(row, employee, ScriptTrialControl.model_validate(
            command.model_dump(include=set(ScriptTrialControl.model_fields))
        ))
        if row.status != "preparing" or (
            row.deadline_at <= datetime.now(timezone.utc)
        ):
            raise ConflictError("原试验已取消、到期或终止，请查询原请求")
        from app.services.agent_device_script_evidence import (
            attach_case_evidence,
        )
        await attach_case_evidence(
            self.db, project, actor, employee, command.request_id, report
        )
        row.status, row.report_id = "ready", report
        row.completed_at = datetime.now(timezone.utc)

    async def claim(self, trial: AgentDeviceScriptTrial) -> bool:
        await self._lock(trial.project_id)
        row = await self._find(
            trial.project_id, trial.actor_id, trial.request_id
        )
        now = datetime.now(timezone.utc)
        if (
            row is None or row.report_id != trial.id or row.status != "ready"
            or row.stop_status != "none" or row.dispatched_at is not None
        ):
            await self.db.commit()
            return False
        claimed = await self.db.scalar(update(AgentDeviceScriptTrial).where(
            AgentDeviceScriptTrial.id == trial.id,
            AgentDeviceScriptTrial.status == "unknown",
            AgentDeviceScriptTrial.dispatch_deadline_at > now,
        ).values(
            dispatch_deadline_at=now + timedelta(
                seconds=self.trials._reference(trial).timeout_seconds + 30
            )
        ).returning(AgentDeviceScriptTrial.id))
        if claimed is not None:
            row.dispatched_at = now
        await self.db.commit()
        return claimed is not None

    async def observe(
        self, trial: AgentDeviceScriptTrial, receipt: ScriptExecutionRead
    ) -> None:
        receipt = validate_script_receipt(
            self.trials._reference(trial).query(), receipt
        )
        if receipt.status == "unknown":
            return
        await self._lock(trial.project_id)
        row = await self._find(
            trial.project_id, trial.actor_id, trial.request_id
        )
        if row is not None and row.report_id == trial.id and (
            row.stop_status not in {"confirmed", "finished"}
        ):
            row.stop_status = (
                "confirmed" if receipt.status == "canceled"
                and row.stop_requested_at is not None else "finished"
            )
        await self.db.commit()

    async def state(
        self, project: UUID, actor: UUID, employee: UUID, request: UUID,
        *, refresh: bool = True,
    ) -> ScriptTrialRequestState:
        await self.trials.bindings._actor(project, actor)
        await self.trials.bindings._agent(project, employee)
        row = await self._find(project, actor, request)
        if row is None or row.agent_id != employee:
            raise NotFoundError("员工脚本试验请求")
        if row.status == "preparing" and (
            row.deadline_at <= datetime.now(timezone.utc)
        ):
            await self.fail(project, actor, request, "preparation_timeout")
            await self.db.refresh(row)
        report = None
        if row.status == "ready":
            if row.report_id is None:
                raise ConflictError("原请求缺少脚本试验报告")
            report_id = row.report_id
            trial = await self.trials._row(
                project, actor, employee, report=report_id
            )
            await self.db.commit()
            if refresh and row.stop_status == "requested":
                try:
                    raw = await self.trials.device.read_script(
                        self.trials._reference(trial).query()
                    )
                    await self.observe(trial, raw)
                    if trial.status == "unknown":
                        await self.trials._consume(trial, raw)
                except (
                    ConflictError, ExternalServiceError,
                    ValueError, ValidationError,
                ):
                    await self.db.rollback()
                row = await self._find(project, actor, request)
                if row is None:
                    raise NotFoundError("员工脚本试验请求")
            report = (
                await self.trials.get(project, actor, employee, report_id)
                if refresh and row.stop_status != "requested"
                else await self.trials._view(await self.trials._row(
                    project, actor, employee, report=report_id
                ))
            )
            row = await self._find(project, actor, request)
            if row is None:
                raise NotFoundError("员工脚本试验请求")
        return ScriptTrialRequestState.model_validate({
            **{name: getattr(row, name) for name in (
                "project_id", "actor_id", "agent_id", "request_id",
                "binding_id", "binding_revision", "status", "stop_status",
                "created_at", "deadline_at", "completed_at", "error_code",
            )},
            "report": report,
        })

    async def cancel(
        self, project: UUID, actor: UUID, employee: UUID,
        command: ScriptTrialControl,
    ) -> ScriptTrialRequestState:
        identity = None
        try:
            async with asyncio.timeout(self.lock_seconds * 2):
                await self.trials.bindings._actor(project, actor)
                await self.trials.bindings._agent(project, employee)
                await self._lock(project)
                row = await self._find(project, actor, command.request_id)
                if row is None:
                    original = await self.db.scalar(select(
                        AgentDeviceScriptTrial
                    ).where(
                        AgentDeviceScriptTrial.project_id == project,
                        AgentDeviceScriptTrial.actor_id == actor,
                        AgentDeviceScriptTrial.request_id
                        == command.request_id,
                    ))
                    if original is not None:
                        row = self._legacy(original)
                    else:
                        binding = await self.trials.bindings._binding(
                            project, employee
                        )
                        if (
                            binding is None or binding.id != command.binding_id
                            or command.binding_revision > binding.revision
                        ):
                            raise ConflictError("原脚本配置范围无法确认")
                        now = datetime.now(timezone.utc)
                        row = AgentDeviceScriptTrialRequest(
                            id=uuid4(), project_id=project, actor_id=actor,
                            agent_id=employee, request_id=command.request_id,
                            binding_id=command.binding_id,
                            binding_revision=command.binding_revision,
                            status="preparing", stop_status="none",
                            created_at=now, deadline_at=now + timedelta(
                                seconds=self.preparation_seconds
                            ),
                        )
                    self.db.add(row)
                self._scope(row, employee, command)
                now = datetime.now(timezone.utc)
                if row.status == "preparing":
                    row.status, row.error_code = "cancelled", "cancelled"
                    row.completed_at, row.stop_requested_at = now, now
                    row.stop_status = "confirmed"
                elif row.status == "ready" and row.stop_status not in {
                    "confirmed", "finished"
                }:
                    if row.report_id is None:
                        raise ConflictError("原请求缺少执行报告")
                    trial = await self.trials._row(
                        project, actor, employee, report=row.report_id
                    )
                    row.stop_requested_at = row.stop_requested_at or now
                    if row.dispatched_at is None:
                        row.stop_status = "confirmed"
                        if trial.status == "unknown":
                            trial.status, trial.error_code = (
                                "failed", "interrupted"
                            )
                            trial.completed_at = now
                    else:
                        row.stop_status = "requested"
                        identity = self.trials._reference(trial).identity
                await self.db.commit()
        except BaseException:
            await self.db.rollback()
            raise
        if identity is not None:
            try:
                await self.trials.device.finish_session(identity, "cancelled")
            except ExternalServiceError:
                pass
        return await self.state(
            project, actor, employee, command.request_id
        )
