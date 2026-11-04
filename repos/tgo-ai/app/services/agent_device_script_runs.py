"""Persist business requests and recover by their original ID."""

import asyncio
import hmac
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from pydantic import JsonValue, ValidationError
from sqlalchemy import and_, or_, select

from app.exceptions import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
)
from app.models.agent_device_script_run import AgentDeviceScriptRun
from app.schemas.agent_device_script_runs import (
    ScriptRunControl,
    ScriptRunStart,
    ScriptRunView,
)
from app.schemas.agent_device_script_reviews import ScriptRunCheck
from app.schemas.agent_device_script_source import ScriptRunSource
from app.schemas.agent_device_script_trials import ScriptTrialReference
from app.schemas.device_script_execution import (
    BusinessValue,
    PreparedScriptCall,
    ScriptExecutionRead,
    validate_script_receipt,
)
from app.schemas.device_session import DeviceExecutionIdentity
from app.services.agent_activation import lock_account_agents
from app.services.agent_device_script_cases import (
    AgentDeviceScriptCaseService,
)
from app.services.agent_device_script_reviews import (
    AgentDeviceScriptReviews,
)
from app.services.agent_device_script_run_source import (
    ScriptSourceGuard, read_source, seal_source,
)
from app.services.agent_trial_fingerprint import private_digest
from app.services.device_execution import track_device_identity
from app.services.device_script_preparation import prepare_script_call


class AgentDeviceScriptRuns:
    def __init__(
        self, cases: AgentDeviceScriptCaseService,
        *, source_guard: ScriptSourceGuard | None = None,
    ) -> None:
        self.cases, self.db, self.trials = cases, cases.db, cases.trials
        self.reviews = AgentDeviceScriptReviews(cases)
        self.source_guard = source_guard

    async def _check_source(
        self, project: UUID, agent: UUID, source: ScriptRunSource | None,
    ) -> None:
        if source is not None:
            if (
                self.source_guard is None
                or source.reference.project_id != project
                or source.reference.agent_id != agent
            ):
                raise ConflictError("员工运行来源或可信检查器缺失")
            await self.source_guard(source)

    async def _row(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
    ) -> AgentDeviceScriptRun:
        row: AgentDeviceScriptRun | None = await self.db.scalar(
            select(AgentDeviceScriptRun)
            .where(
                AgentDeviceScriptRun.project_id == project,
                AgentDeviceScriptRun.actor_id == actor,
                AgentDeviceScriptRun.request_id == request,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("员工脚本业务执行")
        if row.agent_id != agent or row.deleted_at is not None:
            raise ConflictError("原业务执行请求范围不一致")
        read_source(row)
        return row

    @staticmethod
    def _view(
        row: AgentDeviceScriptRun,
        fields: dict[str, BusinessValue] | None = None,
    ) -> ScriptRunView:
        return ScriptRunView.model_validate(
            {
                **{
                    name: getattr(row, name)
                    for name in ScriptRunView.model_fields
                    if name
                    not in {"scope", "can_enable", "result_fields"}
                },
                "result_fields": fields,
            }
        )

    @staticmethod
    def _digest(
        row: AgentDeviceScriptRun, ref: ScriptTrialReference
    ) -> str:
        source = read_source(row)
        return private_digest(
            {
                **({"source": source.model_dump(mode="json")}
                   if source is not None else {}),
                "purpose": "approved-business-script-run-v1",
                "identity": {
                    name: str(getattr(row, name))
                    for name in (
                        "id",
                        "project_id",
                        "actor_id",
                        "agent_id",
                        "request_id",
                        "report_id",
                        "review_id",
                        "command_digest",
                    )
                },
                "created_at": row.created_at.isoformat(),
                "deadline_at": row.deadline_at.isoformat(),
                "reference": ref.model_dump(mode="json"),
            }
        )

    def _reference(
        self, row: AgentDeviceScriptRun
    ) -> ScriptTrialReference:
        try:
            ref = ScriptTrialReference.model_validate_json(
                row.reference_json or ""
            )
            source = read_source(row)
            if (
                ref.identity.project_id != row.project_id
                or ref.identity.agent_id != row.agent_id
                or ref.execution_id != row.execution_id
                or source is not None and (
                    ref.identity.workflow_execution_id
                    != source.workflow_execution_id
                )
                or not hmac.compare_digest(
                    self._digest(row, ref), row.reference_digest or ""
                )
            ):
                raise ValueError("Business reference changed")
            return ref
        except (ValueError, ValidationError):
            raise ConflictError("原业务执行身份无法核验") from None

    async def _approval(self, row: AgentDeviceScriptRun) -> None:
        trial = await self.trials._row(
            row.project_id,
            row.actor_id,
            row.agent_id,
            report=row.report_id,
        )
        state = await self.reviews._view(
            trial,
            await self.reviews._review(trial),
        )
        if (
            state.status != "approved"
            or state.review is None
            or state.review.id != row.review_id
        ):
            raise ConflictError("原业务审批已失效，请重新测试和评审")

    async def _register(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptRunControl,
        digest: str | None,
        source: ScriptRunSource | None = None,
    ) -> tuple[AgentDeviceScriptRun, bool]:
        await self.cases._authorize(project, actor, agent)
        await lock_account_agents(self.db, project)
        row: AgentDeviceScriptRun | None
        try:
            row = await self._row(
                project, actor, agent, command.request_id
            )
        except NotFoundError:
            row = None
        if row is not None:
            if digest is not None and read_source(row) != source:
                raise ConflictError("原业务执行的员工运行来源不一致")
            if (
                row.report_id != command.report_id
                or row.review_id != command.review_id
                or digest is not None
                and row.command_digest is not None
                and not hmac.compare_digest(row.command_digest, digest)
            ):
                raise ConflictError("原业务执行请求内容不一致")
            self._expire(row)
            await self.db.commit()
            return row, False
        now = datetime.now(timezone.utc)
        row = AgentDeviceScriptRun(
            id=uuid4(),
            project_id=project,
            actor_id=actor,
            agent_id=agent,
            request_id=command.request_id,
            report_id=command.report_id,
            review_id=command.review_id,
            command_digest=digest,
            status="preparing",
            created_at=now,
            deadline_at=now + timedelta(seconds=60),
        )
        seal_source(row, source)
        if digest is not None:
            await self._approval(row)
            pending = await self.db.scalar(
                select(AgentDeviceScriptRun.id)
                .where(
                    AgentDeviceScriptRun.project_id == project,
                    AgentDeviceScriptRun.agent_id == agent,
                    or_(
                        AgentDeviceScriptRun.status == "unknown",
                        and_(
                            AgentDeviceScriptRun.status == "preparing",
                            AgentDeviceScriptRun.deadline_at > now,
                        ),
                    ),
                )
                .limit(1)
            )
            if pending is not None:
                raise ConflictError("员工仍有未确认的业务执行，请查询原编号")
        else:
            trial = await self.trials._row(
                project,
                actor,
                agent,
                report=command.report_id,
            )
            review = await self.reviews._review(trial)
            if review is None or review.id != command.review_id:
                raise ConflictError("原业务评审范围无法确认")
            row.status, row.error_code = "cancelled", "cancelled"
            row.completed_at, row.stop_requested_at = now, now
        self.db.add(row)
        await self.db.commit()
        return row, digest is not None

    @staticmethod
    def _expire(row: AgentDeviceScriptRun) -> None:
        if row.status == "preparing" and (
            row.deadline_at <= datetime.now(timezone.utc)
        ):
            row.status, row.error_code = "failed", "preparation_expired"
            row.completed_at = datetime.now(timezone.utc)

    async def _gate(self, row: AgentDeviceScriptRun) -> ScriptRunCheck:
        check = await self.reviews.check_run(
            row.project_id,
            row.actor_id,
            row.agent_id,
            row.report_id,
        )
        await self._check_source(
            row.project_id, row.agent_id, read_source(row),
        )
        if (
            check.status != "ready"
            or check.review.review is None
            or check.review.review.id != row.review_id
            or check.valid_until is None
            or check.valid_until <= datetime.now(timezone.utc)
        ):
            raise ConflictError("当前审批、设备或剩余次数不满足执行条件")
        return check

    async def _prepare(
        self,
        row: AgentDeviceScriptRun,
        command: ScriptRunStart,
    ) -> PreparedScriptCall:
        await self._gate(row)
        trial = await self.trials._row(
            row.project_id,
            row.actor_id,
            row.agent_id,
            report=row.report_id,
        )
        original = self.trials._reference(trial)
        budget = await self.trials._current(trial)
        (
            bound,
            item,
        ) = await self.trials.device.validate_script_configuration(
            row.project_id,
            trial.device_id,
        )
        if (
            item.model_dump_json()
            != original.descriptor.model_dump_json()
            or bound.policy.digest != original.policy_digest
        ):
            raise ConflictError("已评审的脚本定义发生变化")
        source = read_source(row)
        identity = DeviceExecutionIdentity(
            project_id=row.project_id,
            device_id=trial.device_id,
            session_id=uuid4(),
            agent_id=row.agent_id,
            agent_name=("AI 员工已批准脚本业务" if source is not None
                        else "已批准的员工脚本业务"),
            workflow_execution_id=(source.workflow_execution_id
                                   if source is not None else None),
        )
        call = prepare_script_call(
            identity,
            bound,
            item,
            command.parameters,
            uuid4(),
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
        await self._check_source(row.project_id, row.agent_id, source)
        await lock_account_agents(self.db, row.project_id)
        row = await self._row(
            row.project_id,
            row.actor_id,
            row.agent_id,
            row.request_id,
        )
        self._expire(row)
        await self._approval(row)
        if row.status != "preparing":
            raise ConflictError("原业务准备已结束，请查询原编号")
        row.execution_id = ref.execution_id
        row.reference_json = ref.model_dump_json()
        if len(row.reference_json.encode()) > 131072:
            raise ConflictError("审核元数据过大，无法建立业务执行")
        row.reference_digest = self._digest(row, ref)
        await self.db.commit()
        return call

    async def _consume(
        self,
        row: AgentDeviceScriptRun,
        raw: ScriptExecutionRead,
    ) -> dict[str, BusinessValue] | None:
        receipt = validate_script_receipt(
            self._reference(row).query(), raw
        )
        await lock_account_agents(self.db, row.project_id)
        row = await self._row(
            row.project_id,
            row.actor_id,
            row.agent_id,
            row.request_id,
        )
        if receipt.status == "unknown":
            await self.db.commit()
            return None
        fields = receipt.fields if receipt.result_available else None
        values: dict[str, JsonValue] = (
            dict(fields) if fields is not None else {}
        )
        digest = private_digest(values) if fields is not None else None
        if row.status == "unknown":
            row.status = (
                "succeeded"
                if receipt.status == "succeeded"
                else "cancelled"
                if receipt.status == "canceled"
                else "failed"
            )
            row.result_digest = digest
            row.error_code = (
                None
                if row.status == "succeeded" and fields is not None
                else "result_unavailable"
                if row.status == "succeeded"
                else "cancelled"
                if row.status == "cancelled"
                else "execution_failed"
            )
            row.completed_at = datetime.now(timezone.utc)
        elif fields is not None and (
            row.status != "succeeded" or digest != row.result_digest
        ):
            raise ConflictError("原业务结果摘要不一致")
        await self.db.commit()
        return fields

    async def start(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptRunStart,
        *, source: ScriptRunSource | None = None,
    ) -> ScriptRunView:
        if source is not None:
            source = ScriptRunSource.model_validate_json(
                source.model_dump_json(),
            )
        command = ScriptRunStart.model_validate_json(
            command.model_dump_json()
        )
        await self._check_source(project, agent, source)
        digest = private_digest(
            {
                "agent_id": str(agent),
                "command": command.model_dump(mode="json"),
            }
        )
        row, owns_dispatch = await self._register(
            project,
            actor,
            agent,
            command,
            digest,
            source,
        )
        if not owns_dispatch:
            return self._view(row)
        fields = None
        try:
            call = await self._prepare(row, command)
            async with track_device_identity(
                call.identity, self.trials.device
            ) as execution:
                checked = await self._gate(row)
                await lock_account_agents(self.db, project)
                row = await self._row(
                    project, actor, agent, command.request_id
                )
                self._expire(row)
                await self._approval(row)
                if (
                    row.status != "preparing"
                    or row.stop_requested_at
                    or checked.valid_until is None
                    or checked.valid_until <= datetime.now(timezone.utc)
                ):
                    raise ConflictError("原业务执行已经取消或结束")
                row.status, row.error_code = "unknown", "result_unknown"
                row.dispatch_started_at = datetime.now(timezone.utc)
                await self.db.commit()
                fields = await self._consume(
                    row,
                    await self.trials.device.execute_script(call),
                )
                execution.complete(row.status == "succeeded")
        except asyncio.CancelledError:
            await self.db.rollback()
            raise
        except (
            ConflictError,
            ExternalServiceError,
            ValueError,
            ValidationError,
        ) as error:
            await self.db.rollback()
            row = await self._row(
                project, actor, agent, command.request_id
            )
            if row.status == "preparing":
                row.status, row.error_code = (
                    "failed",
                    "conditions_changed",
                )
                row.completed_at = datetime.now(timezone.utc)
            elif row.status == "unknown" and isinstance(
                error, ValueError
            ):
                row.error_code = "invalid_receipt"
            await self.db.commit()
        await self.cases._authorize(project, actor, agent)
        await self._check_source(project, agent, source)
        return self._view(row, fields)

    async def get(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
    ) -> ScriptRunView:
        await self.cases._authorize(project, actor, agent)
        await lock_account_agents(self.db, project)
        row = await self._row(project, actor, agent, request)
        self._expire(row)
        await self.db.commit()
        fields = None
        if (
            row.reference_json
            and row.dispatch_started_at
            and row.status
            in {
                "unknown",
                "succeeded",
            }
        ):
            try:
                raw = await self.trials.device.read_script(
                    self._reference(row).query(),
                )
                fields = await self._consume(row, raw)
            except (
                ExternalServiceError,
                ValueError,
                ValidationError,
            ) as error:
                await self.db.rollback()
                row = await self._row(project, actor, agent, request)
                if row.status == "unknown" and isinstance(
                    error, ValueError
                ):
                    row.error_code = "invalid_receipt"
                    await self.db.commit()
        await self.cases._authorize(project, actor, agent)
        return self._view(row, fields)

    async def cancel(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
        report: UUID,
        review: UUID,
    ) -> ScriptRunView:
        command = ScriptRunControl(
            request_id=request,
            report_id=report,
            review_id=review,
        )
        row, _ = await self._register(
            project, actor, agent, command, None
        )
        await lock_account_agents(self.db, project)
        row = await self._row(project, actor, agent, request)
        if row.status in {"preparing", "unknown"}:
            row.stop_requested_at = datetime.now(timezone.utc)
            if row.status == "preparing":
                row.status, row.error_code = "cancelled", "cancelled"
                row.completed_at = row.stop_requested_at
        await self.db.commit()
        if row.reference_json and row.status in {
            "cancelled",
            "unknown",
        }:
            try:
                await self.trials.device.finish_session(
                    self._reference(row).identity,
                    "cancelled",
                )
            except ExternalServiceError:
                pass
        return await self.get(project, actor, agent, request)
