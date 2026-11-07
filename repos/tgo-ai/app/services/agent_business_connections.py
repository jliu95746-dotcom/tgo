"""Serialize paused employee configuration; no query or activation grant."""
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.models.agent import Agent
from app.models.agent_business_connection import (
    AgentBusinessConnection,
    AgentBusinessConnectionRequest,
)
from app.schemas.agent_business_connections import (
    AgentBusinessConnectionRecord,
    AgentBusinessConnections,
    AgentBusinessConnectionWrite,
    BusinessConnectionOwner,
    ConnectionOwnerQuery,
)
from app.schemas.agent_configuration_actor import (
    AgentConfigurationActor,
    AgentConfigurationActorQuery,
)
from app.services.agent_activation import lock_account_agents
from app.services.agent_trial_fingerprint import private_digest
from app.services.api_service import api_service_client

json_object = TypeAdapter(dict[str, JsonValue])


class ConnectionOwner(Protocol):
    async def get_agent_configuration_actor(
        self, query: AgentConfigurationActorQuery
    ) -> AgentConfigurationActor:
        ...

    async def get_agent_business_connection(
        self, query: ConnectionOwnerQuery
    ) -> BusinessConnectionOwner:
        ...


def fresh(value: datetime) -> bool:
    return (
        value.tzinfo is not None
        and -30 <= (datetime.now(UTC) - value).total_seconds() <= 60
    )


class AgentBusinessConnectionService:
    def __init__(
        self, db: AsyncSession, owner: ConnectionOwner = api_service_client
    ):
        self.db, self.owner = db, owner

    async def _actor(self, project: UUID, actor: UUID) -> None:
        result = await self.owner.get_agent_configuration_actor(
            AgentConfigurationActorQuery(
                project_id=project, requested_by=actor
            )
        )
        if (
            result.project_id != project
            or result.requested_by != actor
            or not fresh(result.checked_at)
        ):
            raise ExternalServiceError("tgo-api", "无法核验当前配置权限，请重试")

    async def _agent(self, project: UUID, agent_id: UUID) -> Agent:
        agent = await self.db.scalar(
            select(Agent)
            .where(
                Agent.project_id == project,
                Agent.id == agent_id,
                Agent.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if agent is None:
            raise NotFoundError("员工")
        return agent

    @staticmethod
    def record(row: AgentBusinessConnection) -> AgentBusinessConnectionRecord:
        return AgentBusinessConnectionRecord.model_validate(
            {
                key: getattr(row, key)
                for key in (
                    "id",
                    "project_id",
                    "agent_id",
                    "connection_id",
                    "operations",
                    "enabled",
                    "revision",
                    "configuration_revision",
                    "authorization_revision",
                    "resource_revision",
                    "configured_by",
                )
            }
        )

    async def list(
        self, project: UUID, actor: UUID, agent: UUID
    ) -> AgentBusinessConnections:
        await self._actor(project, actor)
        await self._agent(project, agent)
        rows = await self.db.scalars(
            select(AgentBusinessConnection)
            .where(
                AgentBusinessConnection.project_id == project,
                AgentBusinessConnection.agent_id == agent,
            )
            .order_by(AgentBusinessConnection.connection_id)
            .execution_options(populate_existing=True)
        )
        return AgentBusinessConnections(
            project_id=project,
            agent_id=agent,
            bindings=[self.record(row) for row in rows],
        )

    async def _request(
        self, project: UUID, actor: UUID, request: UUID
    ) -> AgentBusinessConnectionRequest | None:
        result: AgentBusinessConnectionRequest | None = await self.db.scalar(
            select(AgentBusinessConnectionRequest)
            .where(
                AgentBusinessConnectionRequest.project_id == project,
                AgentBusinessConnectionRequest.requested_by == actor,
                AgentBusinessConnectionRequest.request_id == request,
            )
            .execution_options(populate_existing=True)
        )
        return result

    async def request_result(
        self, project: UUID, actor: UUID, agent: UUID, request: UUID
    ) -> AgentBusinessConnectionRecord:
        await self._actor(project, actor)
        await self._agent(project, agent)
        row = await self._request(project, actor, request)
        if row is None or row.agent_id != agent:
            raise NotFoundError("连接配置请求")
        return AgentBusinessConnectionRecord.model_validate_json(
            row.result_json
        )

    async def save(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        connection: UUID,
        command: AgentBusinessConnectionWrite,
    ) -> AgentBusinessConnectionRecord:
        try:
            result = await self._save(
                project, actor, agent, connection, command
            )
            await self.db.commit()
            return result
        except Exception:
            await self.db.rollback()
            raise

    async def _save(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        connection: UUID,
        command: AgentBusinessConnectionWrite,
    ) -> AgentBusinessConnectionRecord:
        await self._actor(project, actor)
        await self._agent(project, agent_id)
        await lock_account_agents(self.db, project)
        agent = await self._agent(project, agent_id)
        digest = private_digest(
            json_object.validate_python(
                {
                    "agent_id": str(agent_id),
                    "connection_id": str(connection),
                    "command": command.model_dump(mode="json"),
                }
            )
        )
        previous = await self._request(project, actor, command.request_id)
        if previous:
            if (
                previous.agent_id != agent_id
                or previous.command_digest != digest
            ):
                raise ConflictError("原请求内容不一致，请重新读取配置")
            return AgentBusinessConnectionRecord.model_validate_json(
                previous.result_json
            )
        if agent.is_active:
            raise ConflictError("请先停用员工再修改业务连接")
        row = await self.db.scalar(
            select(AgentBusinessConnection)
            .where(
                AgentBusinessConnection.project_id == project,
                AgentBusinessConnection.agent_id == agent_id,
                AgentBusinessConnection.connection_id == connection,
            )
            .execution_options(populate_existing=True)
        )
        if (row.revision if row else 0) != command.expected_revision:
            raise ConflictError("业务连接配置已有新版本，请重新读取")
        if command.enabled:
            query = ConnectionOwnerQuery(
                project_id=project,
                requested_by=actor,
                connection_id=connection,
            )
            owner = await self.owner.get_agent_business_connection(query)
            if any(
                getattr(owner, field) != value
                for field, value in query.model_dump().items()
            ) or not fresh(owner.checked_at):
                raise ExternalServiceError("tgo-api", "连接范围或核验结果已失效")
            if owner.mode != "http" or not set(command.operations).issubset(
                owner.operations
            ):
                raise ConflictError("连接不支持所选真实查询，请重新选择")
            if row is None:
                row = AgentBusinessConnection(
                    project_id=project,
                    agent_id=agent_id,
                    connection_id=connection,
                    revision=0,
                )
                self.db.add(row)
            row.configuration_revision = owner.configuration_revision
            row.authorization_revision = owner.authorization_revision
            row.resource_revision = owner.resource_revision
        elif row is None:
            raise NotFoundError("员工业务连接")
        await self._actor(project, actor)
        row.operations = list(command.operations)
        row.enabled = command.enabled
        row.configured_by = actor
        row.revision += 1
        await self.db.flush()
        result = self.record(row)
        self.db.add(
            AgentBusinessConnectionRequest(
                project_id=project,
                requested_by=actor,
                agent_id=agent_id,
                request_id=command.request_id,
                command_digest=digest,
                result_json=result.model_dump_json(),
            )
        )
        await self.db.flush()
        return result
