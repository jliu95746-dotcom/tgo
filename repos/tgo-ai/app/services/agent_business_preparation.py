"""Bounded preparation, original request receipts, and cancellation fencing."""

import asyncio
import hmac
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.exceptions import ConflictError, NotFoundError
from app.models.agent_business_request import AgentBusinessRequest
from app.models.agent_business_trial import AgentBusinessTrial
from app.schemas.agent_business_requests import (
    BusinessRequestControl,
    BusinessRequestState,
    PreparationError,
)
from app.schemas.agent_business_trials import (
    BusinessTrialCommand,
    BusinessTrialIdentity,
    BusinessTrialReport,
    BusinessTrialRequest,
)
from app.services.agent_activation import lock_account_agents

if TYPE_CHECKING:
    from app.services.agent_business_trials import AgentBusinessTrialService


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AgentBusinessPreparation:
    def __init__(
        self,
        trials: "AgentBusinessTrialService",
        *,
        preparation_seconds: float = 15,
        lock_seconds: float = 5,
    ):
        self.trials, self.db = trials, trials.db
        self.preparation_seconds, self.lock_seconds = preparation_seconds, lock_seconds

    async def _lock(self, project: UUID) -> None:
        # Cancellation interrupts SQL advisory-lock waits too; the caller rolls back.
        async with asyncio.timeout(self.lock_seconds):
            await lock_account_agents(self.db, project)

    async def _find(
        self, project: UUID, actor: UUID, request: UUID
    ) -> AgentBusinessRequest | None:
        row: AgentBusinessRequest | None = await self.db.scalar(
            select(AgentBusinessRequest)
            .where(
                AgentBusinessRequest.project_id == project,
                AgentBusinessRequest.actor_id == actor,
                AgentBusinessRequest.request_id == request,
            )
            .execution_options(populate_existing=True)
        )
        return row

    @staticmethod
    def _scope(
        row: AgentBusinessRequest, agent: UUID, control: BusinessRequestControl
    ) -> None:
        if (
            row.agent_id != agent
            or row.connection_id != control.connection_id
            or row.operation != control.operation
        ):
            raise ConflictError("原业务测试请求范围不一致")

    async def register(
        self, project: UUID, actor: UUID, agent: UUID, request: BusinessTrialRequest
    ) -> tuple[AgentBusinessRequest, bool]:
        digest = self.trials._digest(agent, request)
        control = BusinessRequestControl.model_validate(
            request.model_dump(include=set(BusinessRequestControl.model_fields))
        )
        try:
            async with asyncio.timeout(self.lock_seconds):
                await self.trials._actor(project, actor)
                await self.trials._agent(project, agent)
                await self._lock(project)
                existing = await self._find(project, actor, request.request_id)
                if existing is not None:
                    self._scope(existing, agent, control)
                    if existing.command_digest is not None and not hmac.compare_digest(
                        existing.command_digest, digest
                    ):
                        raise ConflictError("原业务测试请求内容不一致")
                    await self.db.commit()
                    return existing, False
                pending = await self.db.scalar(
                    select(AgentBusinessRequest.id)
                    .where(
                        AgentBusinessRequest.project_id == project,
                        AgentBusinessRequest.agent_id == agent,
                        AgentBusinessRequest.status == "preparing",
                        AgentBusinessRequest.deadline_at > datetime.now(UTC),
                    )
                    .limit(1)
                )
                running = await self.db.scalar(
                    select(AgentBusinessTrial.id)
                    .where(
                        AgentBusinessTrial.project_id == project,
                        AgentBusinessTrial.agent_id == agent,
                        AgentBusinessTrial.status == "running",
                        AgentBusinessTrial.expires_at > datetime.now(UTC),
                        AgentBusinessTrial.deleted_at.is_(None),
                    )
                    .limit(1)
                )
                if pending is not None or running is not None:
                    raise ConflictError(
                        "该员工已有业务测试正在准备或运行，请查询或取消原请求"
                    )
                now = datetime.now(UTC)
                row = AgentBusinessRequest(
                    id=uuid4(),
                    project_id=project,
                    actor_id=actor,
                    agent_id=agent,
                    connection_id=request.connection_id,
                    request_id=request.request_id,
                    operation=request.operation,
                    command_digest=digest,
                    status="preparing",
                    created_at=now,
                    deadline_at=now + timedelta(seconds=self.preparation_seconds),
                )
                self.db.add(row)
                await self.db.commit()
                return row, True
        except BaseException:
            await self.db.rollback()
            raise

    async def _terminate(
        self, row: AgentBusinessRequest, error: PreparationError
    ) -> None:
        async with asyncio.timeout(self.lock_seconds):
            await self.db.execute(
                update(AgentBusinessRequest)
                .where(
                    AgentBusinessRequest.id == row.id,
                    AgentBusinessRequest.status == "preparing",
                )
                .values(
                    status="failed", error_code=error, completed_at=datetime.now(UTC)
                )
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()

    async def _view(self, row: AgentBusinessRequest) -> BusinessRequestState:
        if row.status == "preparing" and utc(row.deadline_at) <= datetime.now(UTC):
            await self._terminate(row, "preparation_timeout")
            await self.db.refresh(row)
        report = None
        if row.status == "ready":
            if row.report_id is None:
                raise ConflictError("原请求缺少报告编号")
            original = await self.trials._row(
                row.project_id, row.actor_id, row.agent_id, report=row.report_id
            )
            report = self.trials.view(await self.trials._expire(original))
        return BusinessRequestState.model_validate(
            {
                **{
                    key: getattr(row, key)
                    for key in BusinessRequestState.model_fields
                    if key not in {"scope", "can_enable", "report"}
                },
                "report": report,
            }
        )

    async def state(
        self, project: UUID, actor: UUID, agent: UUID, request: UUID
    ) -> BusinessRequestState:
        await self.trials._actor(project, actor)
        await self.trials._agent(project, agent)
        row = await self._find(project, actor, request)
        if row is None:
            # Compatibility with a pre-upgrade report or a fixture that owns only a report.
            original = await self.trials._row(project, actor, agent, request=request)
            report = self.trials.view(await self.trials._expire(original))
            return BusinessRequestState(
                project_id=project,
                actor_id=actor,
                agent_id=agent,
                request_id=request,
                connection_id=report.connection_id,
                operation=report.operation,
                status="ready",
                created_at=report.created_at,
                deadline_at=report.expires_at,
                completed_at=report.created_at,
                error_code=None,
                report=report,
            )
        if row.agent_id != agent:
            raise NotFoundError("业务测试请求")
        return await self._view(row)

    async def _install(
        self, row: AgentBusinessRequest, command: BusinessTrialCommand
    ) -> BusinessTrialReport:
        await self._lock(command.project_id)
        current = await self._find(
            command.project_id, command.actor_id, command.request_id
        )
        if (
            current is None
            or current.id != row.id
            or current.status != "preparing"
            or utc(current.deadline_at) <= datetime.now(UTC)
        ):
            raise ConflictError("原请求已取消、到期或终止，请查询原请求结果")
        employee = await self.trials._agent(command.project_id, command.agent_id)
        if employee.is_active:
            raise ConflictError("请先停用员工再进行独立业务测试")
        if current.command_digest is None:
            raise ConflictError("原请求缺少准备摘要")
        values = command.model_dump(include=set(BusinessTrialIdentity.model_fields))
        values["id"] = values.pop("report_id")
        report = AgentBusinessTrial(
            **values,
            command_digest=current.command_digest,
            status="running",
            created_at=datetime.now(UTC),
            expires_at=min(
                datetime.now(UTC) + timedelta(seconds=180),
                command.configuration_test_valid_until,
            ),
        )
        self.db.add(report)
        current.status, current.report_id, current.completed_at = (
            "ready",
            report.id,
            datetime.now(UTC),
        )
        await self.db.commit()
        return self.trials.view(report)

    async def prepare(
        self, project: UUID, actor: UUID, agent: UUID, request: BusinessTrialRequest
    ) -> tuple[BusinessTrialReport, BusinessTrialCommand | None]:
        row, created = await self.register(project, actor, agent, request)
        if not created:
            state = await self._view(row)
            if state.report is not None:
                return state.report, None
            raise ConflictError("原请求正在准备或已终止，请查询或取消原请求")
        try:
            remaining = max(
                0, (utc(row.deadline_at) - datetime.now(UTC)).total_seconds()
            )
            async with asyncio.timeout(remaining):
                binding, owner, fingerprint = await self.trials._current(
                    project, actor, agent, request.connection_id, request.operation
                )
                proof = next(
                    item
                    for item in owner.configuration_tests
                    if item.operation == request.operation
                )
                if proof.report_id is None or proof.valid_until is None:
                    raise ConflictError("连接配置测试缺少可核验依据")
                command = BusinessTrialCommand(
                    project_id=project,
                    actor_id=actor,
                    agent_id=agent,
                    connection_id=request.connection_id,
                    report_id=uuid4(),
                    request_id=request.request_id,
                    attempt_id=uuid4(),
                    operation=request.operation,
                    binding_id=binding.id,
                    binding_revision=binding.revision,
                    configuration_fingerprint=fingerprint,
                    expected_revision=owner.revision,
                    configuration_revision=owner.configuration_revision,
                    authorization_revision=owner.authorization_revision,
                    resource_revision=owner.resource_revision,
                    configuration_test_id=proof.report_id,
                    configuration_test_valid_until=proof.valid_until,
                    visitor_id=request.visitor_id,
                    order_no=request.order_no,
                    customer_session=request.customer_session,
                )
                return await self._install(row, command), command
        except BaseException as error:
            await self.db.rollback()
            category: PreparationError = (
                "preparation_timeout"
                if isinstance(error, TimeoutError)
                else "interrupted"
                if isinstance(error, asyncio.CancelledError)
                else "preparation_failed"
            )
            # A cancelled transaction expires ORM attributes; keep the row identity usable.
            persisted = await self._find(project, actor, request.request_id)
            if persisted is not None:
                await self._terminate(persisted, category)
            if isinstance(error, TimeoutError):
                raise ConflictError("业务测试准备已到期，请查询原请求结果") from None
            raise

    async def cancel(
        self, project: UUID, actor: UUID, agent: UUID, control: BusinessRequestControl
    ) -> BusinessRequestState:
        try:
            async with asyncio.timeout(self.lock_seconds * 2):
                await self.trials._actor(project, actor)
                await self.trials._agent(project, agent)
                await self._lock(project)
                row = await self._find(project, actor, control.request_id)
                if row is None:
                    original: AgentBusinessTrial | None = await self.db.scalar(
                        select(AgentBusinessTrial)
                        .where(
                            AgentBusinessTrial.project_id == project,
                            AgentBusinessTrial.actor_id == actor,
                            AgentBusinessTrial.request_id == control.request_id,
                        )
                        .execution_options(populate_existing=True)
                    )
                    now = datetime.now(UTC)
                    row = AgentBusinessRequest(
                        id=uuid4(),
                        project_id=project,
                        actor_id=actor,
                        agent_id=agent,
                        request_id=control.request_id,
                        connection_id=control.connection_id,
                        operation=control.operation,
                        status="cancelled",
                        error_code="cancelled",
                        created_at=now,
                        completed_at=now,
                        deadline_at=now + timedelta(seconds=self.preparation_seconds),
                    )
                    if original is not None:
                        if (
                            original.agent_id != agent
                            or original.connection_id != control.connection_id
                            or original.operation != control.operation
                        ):
                            raise ConflictError("原业务测试请求范围不一致")
                        row.status, row.error_code = "ready", None
                        row.report_id, row.command_digest = (
                            original.id,
                            original.command_digest,
                        )
                    self.db.add(row)
                self._scope(row, agent, control)
                if row.status == "preparing":
                    row.status, row.error_code, row.completed_at = (
                        "cancelled",
                        "cancelled",
                        datetime.now(UTC),
                    )
                elif row.status == "ready":
                    await self.db.execute(
                        update(AgentBusinessTrial)
                        .where(
                            AgentBusinessTrial.id == row.report_id,
                            AgentBusinessTrial.project_id == project,
                            AgentBusinessTrial.actor_id == actor,
                            AgentBusinessTrial.agent_id == agent,
                            AgentBusinessTrial.request_id == control.request_id,
                            AgentBusinessTrial.status == "running",
                        )
                        .values(
                            status="cancelled",
                            error_code="cancelled",
                            completed_at=datetime.now(UTC),
                        )
                        .execution_options(synchronize_session=False)
                    )
                await self.db.commit()
                return await self._view(row)
        except BaseException:
            await self.db.rollback()
            raise
