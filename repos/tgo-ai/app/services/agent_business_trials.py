"""Durable test lifecycle and one exact read claim; no production grant."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import asyncio
import hmac
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, NotFoundError, ExternalServiceError
from app.models.agent import Agent
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.agent_business_trial import AgentBusinessTrial
from app.schemas.agent_business_connections import (
    BusinessConnectionOwner,
    ConnectionOwnerQuery,
)
from app.schemas.agent_configuration_actor import (
    AgentConfigurationActor,
    AgentConfigurationActorQuery,
)
from app.schemas.agent_business_trials import (
    BusinessTrialRequest,
    BusinessTrialCommand,
    BusinessTrialCheck,
    BusinessTrialAuthority,
    BusinessTrialReport,
    BusinessTrialReceipt,
    BusinessTrialIdentity,
    TrialError,
)
from app.services.agent_business_connections import fresh
from app.services.agent_activation import lock_account_agents
from app.services.agent_trial_fingerprint import (
    private_digest,
    configuration_fingerprint,
)
from app.services.api_service import api_service_client
from app.schemas.agent_business_reviews import (
    BusinessTestEvidenceQuery,
    BusinessTestEvidence,
)
from app.schemas.agent_business_requests import BusinessEmployeeCurrentState


class TrialOwner(Protocol):
    async def get_agent_business_test_evidence(
        self, query: BusinessTestEvidenceQuery
    ) -> BusinessTestEvidence:
        ...

    async def get_agent_configuration_actor(
        self, query: AgentConfigurationActorQuery
    ) -> AgentConfigurationActor:
        ...

    async def get_agent_business_connection(
        self, query: ConnectionOwnerQuery
    ) -> BusinessConnectionOwner:
        ...

    async def query_agent_business_trial(
        self, command: BusinessTrialCommand
    ) -> BusinessTrialReceipt:
        ...


async def current_fingerprint(
    db: AsyncSession, project: UUID, actor: UUID, agent: UUID
) -> str:
    from app.services.agent_service import AgentService
    from app.runtime.supervisor.infrastructure.services import AIServiceClient

    async with AIServiceClient(AgentService(db), project) as loader:
        configuration = await loader.get_trial_agent(
            agent, requested_by=actor, verify_runtime=False
        )
    return configuration_fingerprint(configuration)


def utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )


class AgentBusinessTrialService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        owner: TrialOwner = api_service_client,
        fingerprint: Callable[
            [AsyncSession, UUID, UUID, UUID], Awaitable[str]
        ] = current_fingerprint,
    ):
        self.db, self.owner, self.fingerprint = db, owner, fingerprint

    async def current_employee(
        self, project: UUID, actor: UUID, agent: UUID
    ) -> BusinessEmployeeCurrentState:
        try:
            async with asyncio.timeout(10):
                await self._actor(project, actor)
                await self._agent(project, agent)
                await self._actor(project, actor)
                employee = await self._agent(project, agent)
        except TimeoutError:
            raise ExternalServiceError("tgo-api", "当前员工状态核验超时") from None
        return BusinessEmployeeCurrentState(
            project_id=project,
            actor_id=actor,
            agent_id=agent,
            active=employee.is_active,
            checked_at=datetime.now(UTC),
        )

    async def _actor(self, project: UUID, actor: UUID) -> None:
        proof = await self.owner.get_agent_configuration_actor(
            AgentConfigurationActorQuery(
                project_id=project, requested_by=actor
            )
        )
        if (
            proof.project_id != project
            or proof.requested_by != actor
            or not fresh(proof.checked_at)
        ):
            raise ExternalServiceError("tgo-api", "当前测试权限无法核验")

    async def _agent(self, project: UUID, agent: UUID) -> Agent:
        row: Agent | None = await self.db.scalar(
            select(Agent)
            .where(
                Agent.id == agent,
                Agent.project_id == project,
                Agent.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("员工")
        return row

    async def _current(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        connection: UUID,
        operation: str,
        *,
        require_paused: bool = True,
    ) -> tuple[AgentBusinessConnection, BusinessConnectionOwner, str]:
        await self._actor(project, actor)
        employee = await self._agent(project, agent)
        if employee.is_active and require_paused:
            raise ConflictError("请先停用员工再进行独立业务测试")
        binding: AgentBusinessConnection | None = await self.db.scalar(
            select(AgentBusinessConnection)
            .where(
                AgentBusinessConnection.project_id == project,
                AgentBusinessConnection.agent_id == agent,
                AgentBusinessConnection.connection_id == connection,
                AgentBusinessConnection.enabled.is_(True),
            )
            .execution_options(populate_existing=True)
        )
        if binding is None or operation not in binding.operations:
            raise ConflictError("请先关联此查询操作")
        query = ConnectionOwnerQuery(
            project_id=project, requested_by=actor, connection_id=connection
        )
        owner = await self.owner.get_agent_business_connection(query)
        proof = next(
            (
                row
                for row in owner.configuration_tests
                if row.operation == operation
            ),
            None,
        )
        if (
            any(
                getattr(owner, key) != value
                for key, value in query.model_dump().items()
            )
            or not fresh(owner.checked_at)
            or not owner.enabled
            or owner.mode != "http"
            or operation not in owner.operations
            or owner.configuration_revision != binding.configuration_revision
            or owner.authorization_revision != binding.authorization_revision
            or owner.resource_revision != binding.resource_revision
            or proof is None
            or proof.status != "current"
            or proof.report_id is None
            or proof.valid_until is None
            or utc(proof.valid_until) <= datetime.now(UTC)
        ):
            raise ConflictError("连接或配置测试已失效，请更新关联后重测")
        fingerprint = await self.fingerprint(self.db, project, actor, agent)
        await self._actor(project, actor)
        return binding, owner, fingerprint

    @staticmethod
    def _digest(agent: UUID, request: BusinessTrialRequest) -> str:
        return private_digest(
            {"agent_id": str(agent), "request": request.private_payload()}
        )

    @staticmethod
    def view(row: AgentBusinessTrial) -> BusinessTrialReport:
        values = {
            key: getattr(row, key)
            for key in BusinessTrialReport.model_fields
            if key not in {"scope", "can_enable", "assessment", "receipt"}
        }
        return BusinessTrialReport(
            **values,
            receipt=BusinessTrialReceipt.model_validate_json(row.receipt_json)
            if row.receipt_json
            else None,
        )

    async def _expire(self, row: AgentBusinessTrial) -> AgentBusinessTrial:
        if row.status != "running" or utc(row.expires_at) > datetime.now(UTC):
            return row
        await self.db.execute(
            update(AgentBusinessTrial)
            .where(
                AgentBusinessTrial.id == row.id,
                AgentBusinessTrial.status == "running",
                AgentBusinessTrial.expires_at <= datetime.now(UTC),
            )
            .values(
                status="failed",
                completed_at=datetime.now(UTC),
                error_code="interrupted",
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def _row(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        *,
        request: UUID | None = None,
        report: UUID | None = None,
    ) -> AgentBusinessTrial:
        query = select(AgentBusinessTrial).where(
            AgentBusinessTrial.project_id == project,
            AgentBusinessTrial.actor_id == actor,
            AgentBusinessTrial.agent_id == agent,
            AgentBusinessTrial.deleted_at.is_(None),
        )
        query = (
            query.where(AgentBusinessTrial.request_id == request)
            if request is not None
            else query.where(AgentBusinessTrial.id == report)
        )
        row: AgentBusinessTrial | None = await self.db.scalar(
            query.execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("业务测试请求")
        return row

    async def by_request(
        self, project: UUID, actor: UUID, agent: UUID, request: UUID
    ) -> BusinessTrialReport:
        await self._actor(project, actor)
        await self._agent(project, agent)
        return self.view(
            await self._expire(
                await self._row(project, actor, agent, request=request)
            )
        )

    async def get(
        self, project: UUID, actor: UUID, agent: UUID, report: UUID
    ) -> BusinessTrialReport:
        await self._actor(project, actor)
        await self._agent(project, agent)
        return self.view(
            await self._expire(
                await self._row(project, actor, agent, report=report)
            )
        )

    async def prepare(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: BusinessTrialRequest,
    ) -> tuple[BusinessTrialReport, BusinessTrialCommand | None]:
        from app.services.agent_business_preparation import (
            AgentBusinessPreparation,
        )

        return await AgentBusinessPreparation(self).prepare(
            project, actor, agent, request
        )

    async def authorize(
        self, check: BusinessTrialCheck
    ) -> BusinessTrialAuthority:
        command = check.command
        await self._actor(command.project_id, command.actor_id)
        row = await self._expire(
            await self._row(
                command.project_id,
                command.actor_id,
                command.agent_id,
                report=command.report_id,
            )
        )
        if (
            row.status != "running"
            or row.attempt_id != command.attempt_id
            or not hmac.compare_digest(
                row.command_digest,
                self._digest(command.agent_id, command.request()),
            )
        ):
            raise ConflictError("原业务测试已结束或内容不一致")
        for key in BusinessTrialIdentity.model_fields:
            stored = row.id if key == "report_id" else getattr(row, key)
            actual = getattr(command, key)
            if (
                utc(stored) if isinstance(stored, datetime) else stored
            ) != actual:
                raise ConflictError("原业务测试配置不一致")
        binding, owner, fingerprint = await self._current(
            command.project_id,
            command.actor_id,
            command.agent_id,
            command.connection_id,
            command.operation,
        )
        proof = next(
            item
            for item in owner.configuration_tests
            if item.operation == command.operation
        )
        if (
            binding.id != command.binding_id
            or binding.revision != command.binding_revision
            or fingerprint != command.configuration_fingerprint
            or owner.revision != command.expected_revision
            or proof.report_id != command.configuration_test_id
            or proof.valid_until != command.configuration_test_valid_until
        ):
            raise ConflictError("业务测试期间的配置或依据已改变")
        if check.phase == "before_query":
            result = await self.db.execute(
                update(AgentBusinessTrial)
                .where(
                    AgentBusinessTrial.id == row.id,
                    AgentBusinessTrial.status == "running",
                    AgentBusinessTrial.query_claimed_at.is_(None),
                    AgentBusinessTrial.expires_at > datetime.now(UTC),
                )
                .values(query_claimed_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()
            if cast(CursorResult[tuple[object, ...]], result).rowcount != 1:
                raise ConflictError("这次测试查询已经领取，不能重复执行")
        elif row.query_claimed_at is None:
            raise ConflictError("原业务查询尚未领取")
        # Refresh after external owner checks: cancellation may have committed
        # while a check awaited network IO.

        await self.db.refresh(row)
        if row.status != "running" or utc(row.expires_at) <= datetime.now(UTC):
            raise ConflictError("原业务测试已结束")
        return BusinessTrialAuthority.model_validate(
            {
                **command.identity(),
                "phase": check.phase,
                "checked_at": datetime.now(UTC),
                "expires_at": utc(row.expires_at),
            }
        )

    async def cancel(
        self, project: UUID, actor: UUID, agent: UUID, report: UUID
    ) -> BusinessTrialReport:
        await self.get(project, actor, agent, report)
        await self.db.execute(
            update(AgentBusinessTrial)
            .where(
                AgentBusinessTrial.id == report,
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
        return await self.get(project, actor, agent, report)

    async def finish(
        self,
        command: BusinessTrialCommand,
        receipt: BusinessTrialReceipt | None,
        error: TrialError | None = None,
    ) -> None:
        if receipt is not None:
            if (
                not receipt.matches(command)
                or not fresh(receipt.checked_at)
                or receipt.customer_evidence.expires_at <= datetime.now(UTC)
            ):
                raise ConflictError("业务结果与原测试不一致或已失效")
            await lock_account_agents(self.db, command.project_id)
            await self.authorize(
                BusinessTrialCheck(command=command, phase="after_query")
            )
        elif error is None:
            raise ValueError("失败报告必须提供明确错误分类")
        await self.db.execute(
            update(AgentBusinessTrial)
            .where(
                AgentBusinessTrial.id == command.report_id,
                AgentBusinessTrial.attempt_id == command.attempt_id,
                AgentBusinessTrial.status == "running",
                AgentBusinessTrial.expires_at > datetime.now(UTC),
            )
            .values(
                status="completed" if receipt else "failed",
                error_code=error if receipt is None else receipt.error_code,
                receipt_json=receipt.model_dump_json() if receipt else None,
                completed_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()

    async def run(self, command: BusinessTrialCommand) -> None:
        (
            'One dispatch only. Crashes expire; private inputs are not '
            'replayed.'
        )
        claimed = await self.db.execute(
            update(AgentBusinessTrial)
            .where(
                AgentBusinessTrial.id == command.report_id,
                AgentBusinessTrial.attempt_id == command.attempt_id,
                AgentBusinessTrial.command_digest
                == self._digest(command.agent_id, command.request()),
                AgentBusinessTrial.status == "running",
                AgentBusinessTrial.dispatched_at.is_(None),
                AgentBusinessTrial.expires_at > datetime.now(UTC),
            )
            .values(dispatched_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()
        if cast(CursorResult[tuple[object, ...]], claimed).rowcount != 1:
            return
        try:
            receipt = await self.owner.query_agent_business_trial(command)
            await self.finish(command, receipt)
        except asyncio.CancelledError:
            await self.db.rollback()
            await self.finish(command, None, "interrupted")
            raise
        except Exception:
            # Never store/echo provider exceptions or private command fields.

            await self.db.rollback()
            await self.finish(command, None, "result_unknown")
