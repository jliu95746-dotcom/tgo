"""Paused employee pins: serialized, replayable, without execution approval."""

from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.models.agent import Agent
from app.models.agent_device_script_binding import (
    AgentDeviceScriptBinding,
    AgentDeviceScriptBindingRequest,
)
from app.schemas.agent_configuration_actor import (
    AgentConfigurationActor,
    AgentConfigurationActorQuery,
)
from app.schemas.agent_device_script_binding import (
    AgentDeviceScriptRecord,
    AgentDeviceScriptState,
    AgentDeviceScriptWrite,
)
from app.schemas.device_script_execution import (
    ScriptPolicyRead,
    validate_reviewed_script_configuration,
)
from app.schemas.device_script_policy import ScriptDescriptor
from app.services.agent_activation import lock_account_agents
from app.services.agent_business_connections import fresh
from app.services.agent_trial_fingerprint import private_digest
from app.services.api_service import api_service_client
from app.services.device_control_client import device_control_client


class ConfigurationActorOwner(Protocol):
    async def get_agent_configuration_actor(
        self, query: AgentConfigurationActorQuery
    ) -> AgentConfigurationActor:
        ...


class ScriptConfigurationOwner(Protocol):
    async def validate_script_configuration(
        self, project: UUID, device: UUID
    ) -> tuple[ScriptPolicyRead, ScriptDescriptor]:
        ...


class AgentDeviceScriptBindingService:
    def __init__(
        self,
        db: AsyncSession,
        actor_owner: ConfigurationActorOwner = api_service_client,
        script_owner: ScriptConfigurationOwner = device_control_client,
    ) -> None:
        self.db, self.actor_owner, self.script_owner = (
            db,
            actor_owner,
            script_owner,
        )

    async def _actor(self, project: UUID, actor: UUID) -> None:
        proof = await self.actor_owner.get_agent_configuration_actor(
            AgentConfigurationActorQuery(
                project_id=project, requested_by=actor
            )
        )
        if (
            proof.project_id != project
            or proof.requested_by != actor
            or not fresh(proof.checked_at)
        ):
            raise ExternalServiceError("tgo-api", "当前配置权限无法核验")

    async def _agent(self, project: UUID, employee: UUID) -> Agent:
        row = await self.db.scalar(
            select(Agent)
            .where(
                Agent.id == employee,
                Agent.project_id == project,
                Agent.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("员工")
        return row

    async def _binding(
        self, project: UUID, employee: UUID
    ) -> AgentDeviceScriptBinding | None:
        row: AgentDeviceScriptBinding | None = await self.db.scalar(
            select(AgentDeviceScriptBinding)
            .where(
                AgentDeviceScriptBinding.project_id == project,
                AgentDeviceScriptBinding.agent_id == employee,
                AgentDeviceScriptBinding.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        return row

    @staticmethod
    def record(row: AgentDeviceScriptBinding) -> AgentDeviceScriptRecord:
        return AgentDeviceScriptRecord.model_validate(
            {
                key: getattr(row, key)
                for key in (
                    "id",
                    "project_id",
                    "agent_id",
                    "device_id",
                    "script_id",
                    "policy_digest",
                    "grant_digest",
                    "enabled",
                    "revision",
                    "configured_by",
                )
            }
        )

    async def read(
        self, project: UUID, actor: UUID, employee: UUID
    ) -> AgentDeviceScriptState:
        await self._actor(project, actor)
        await self._agent(project, employee)
        row = await self._binding(project, employee)
        return AgentDeviceScriptState(
            project_id=project,
            agent_id=employee,
            binding=self.record(row) if row else None,
        )

    async def _request(
        self, project: UUID, actor: UUID, request: UUID
    ) -> AgentDeviceScriptBindingRequest | None:
        row: AgentDeviceScriptBindingRequest | None = await self.db.scalar(
            select(AgentDeviceScriptBindingRequest)
            .where(
                AgentDeviceScriptBindingRequest.project_id == project,
                AgentDeviceScriptBindingRequest.requested_by == actor,
                AgentDeviceScriptBindingRequest.request_id == request,
            )
            .execution_options(populate_existing=True)
        )
        return row

    async def request_result(
        self, project: UUID, actor: UUID, employee: UUID, request: UUID
    ) -> AgentDeviceScriptRecord:
        await self._actor(project, actor)
        await self._agent(project, employee)
        row = await self._request(project, actor, request)
        if row is None or row.agent_id != employee:
            raise NotFoundError("员工脚本配置请求")
        return AgentDeviceScriptRecord.model_validate_json(row.result_json)

    async def save(
        self,
        project: UUID,
        actor: UUID,
        employee: UUID,
        command: AgentDeviceScriptWrite,
    ) -> AgentDeviceScriptRecord:
        try:
            result = await self._save(project, actor, employee, command)
            await self.db.commit()
            return result
        except BaseException:
            await self.db.rollback()
            raise

    async def _save(
        self,
        project: UUID,
        actor: UUID,
        employee: UUID,
        command: AgentDeviceScriptWrite,
    ) -> AgentDeviceScriptRecord:
        await self._actor(project, actor)
        await self._agent(project, employee)
        await lock_account_agents(self.db, project)
        agent = await self._agent(project, employee)
        digest = private_digest(
            {
                "agent_id": str(employee),
                "command": command.model_dump(mode="json"),
            }
        )
        old = await self._request(project, actor, command.request_id)
        if old is not None:
            if old.agent_id != employee or old.command_digest != digest:
                raise ConflictError("原脚本配置请求内容不一致，请重新读取")
            return AgentDeviceScriptRecord.model_validate_json(old.result_json)
        if agent.is_active:
            raise ConflictError("请先停用员工，再修改脚本绑定")
        row = await self._binding(project, employee)
        if (row.revision if row else 0) != command.expected_revision:
            raise ConflictError("员工脚本配置已有新版本，请重新读取")
        if command.enabled:
            if agent.bound_device_id or agent.bound_device_policy_digest:
                raise ConflictError("请先解除原设备读取绑定，再配置脚本")
            assert command.device_id is not None
            (
                bound,
                item,
            ) = await self.script_owner.validate_script_configuration(
                project, command.device_id
            )
            try:
                bound, item = validate_reviewed_script_configuration(
                    project, command.device_id, bound, item
                )
            except ValueError:
                raise ExternalServiceError(
                    "device-control", "脚本审核范围已经变化"
                ) from None
            if bound.policy.digest != command.expected_policy_digest:
                raise ConflictError("设备脚本策略已变化，请刷新后重新选择")
            if row is None:
                row = AgentDeviceScriptBinding(
                    project_id=project, agent_id=employee, revision=0
                )
                self.db.add(row)
            row.device_id, row.script_id = command.device_id, item.script_id
            row.policy_digest, row.grant_digest = (
                bound.policy.digest,
                item.grant_digest,
            )
        elif row is None:
            raise NotFoundError("员工脚本绑定")
        assert row is not None
        await self._actor(project, actor)
        row.enabled, row.configured_by = command.enabled, actor
        row.revision += 1
        await self.db.flush()
        result = self.record(row)
        self.db.add(
            AgentDeviceScriptBindingRequest(
                project_id=project,
                requested_by=actor,
                agent_id=employee,
                request_id=command.request_id,
                command_digest=digest,
                result_json=result.model_dump_json(),
            )
        )
        await self.db.flush()
        return result
