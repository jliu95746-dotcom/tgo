"""Move one disabled legacy binding to a verified, fixed publication."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.models.agent import Agent
from app.models.workflow import AgentWorkflow
from app.models.workflow_pin_transition import AgentWorkflowPinTransition
from app.schemas.agent_configuration_actor import (
    AgentConfigurationActor,
    AgentConfigurationActorQuery,
)
from app.schemas.workflow_pin_transition import (
    WorkflowPinTransitionCommand,
    WorkflowPinTransitionHistory,
    WorkflowPinTransitionReceipt,
)
from app.schemas.base import PaginationMetadata
from app.services.agent_activation import lock_account_agents
from app.services.api_service import api_service_client
from app.services.workflow_service import (
    WorkflowIntegrationRelease,
    workflow_service_client,
)


class ActorOwner(Protocol):
    async def get_agent_configuration_actor(
        self, query: AgentConfigurationActorQuery
    ) -> AgentConfigurationActor:
        ...


class ReleaseResolver(Protocol):
    async def resolve_published_bindings(
        self,
        workflow_ids: list[str],
        project_id: str,
        selected_versions: dict[str, int] | None = None,
    ) -> dict[str, WorkflowIntegrationRelease]:
        ...


class WorkflowPinTransitionService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        owner: ActorOwner = api_service_client,
        resolver: ReleaseResolver = workflow_service_client,
    ) -> None:
        self.db, self.owner, self.resolver = db, owner, resolver

    async def history(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> WorkflowPinTransitionHistory:
        await self._actor(project, actor)
        conditions = (
            AgentWorkflowPinTransition.project_id == project,
            AgentWorkflowPinTransition.agent_id == agent_id,
        )
        total = (
            await self.db.scalar(
                select(func.count(AgentWorkflowPinTransition.id)).where(*conditions)
            )
            or 0
        )
        rows = (
            await self.db.scalars(
                select(AgentWorkflowPinTransition)
                .where(*conditions)
                .order_by(
                    AgentWorkflowPinTransition.created_at.desc(),
                    AgentWorkflowPinTransition.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return WorkflowPinTransitionHistory(
            data=[WorkflowPinTransitionReceipt.model_validate(row) for row in rows],
            pagination=PaginationMetadata(
                total=total,
                limit=limit,
                offset=offset,
                has_next=offset + limit < total,
                has_prev=offset > 0,
            ),
        )

    async def _actor(self, project: UUID, actor: UUID) -> None:
        receipt = await self.owner.get_agent_configuration_actor(
            AgentConfigurationActorQuery(project_id=project, requested_by=actor)
        )
        if receipt.checked_at.tzinfo is None:
            raise ExternalServiceError("tgo-api", "无法核验当前配置权限，请重试")
        checked_age = (datetime.now(UTC) - receipt.checked_at).total_seconds()
        if (
            receipt.project_id != project
            or receipt.requested_by != actor
            or not -30 <= checked_age <= 60
        ):
            raise ExternalServiceError("tgo-api", "无法核验当前配置权限，请重试")

    async def pin(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        workflow_id: str,
        command: WorkflowPinTransitionCommand,
    ) -> WorkflowPinTransitionReceipt:
        try:
            result = await self._pin(project, actor, agent_id, workflow_id, command)
            await self.db.commit()
            return result
        except Exception:
            await self.db.rollback()
            raise

    async def _pin(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        workflow_id: str,
        command: WorkflowPinTransitionCommand,
    ) -> WorkflowPinTransitionReceipt:
        await self._actor(project, actor)
        await lock_account_agents(self.db, project)
        previous = await self.db.scalar(
            select(AgentWorkflowPinTransition).where(
                AgentWorkflowPinTransition.project_id == project,
                AgentWorkflowPinTransition.requested_by == actor,
                AgentWorkflowPinTransition.request_id == command.request_id,
            )
        )
        if previous is not None:
            if (
                previous.agent_id != agent_id
                or previous.workflow_id != workflow_id
                or previous.binding_id != command.expected_binding_id
                or previous.release_version != command.release_version
            ):
                raise ConflictError("原请求内容不一致，请重新读取旧关联")
            return WorkflowPinTransitionReceipt.model_validate(previous)

        agent = await self.db.scalar(
            select(Agent)
            .where(
                Agent.id == agent_id,
                Agent.project_id == project,
                Agent.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if agent is None:
            raise NotFoundError("员工", agent_id)
        binding = await self.db.scalar(
            select(AgentWorkflow)
            .where(
                AgentWorkflow.id == command.expected_binding_id,
                AgentWorkflow.agent_id == agent_id,
                AgentWorkflow.workflow_id == workflow_id,
                AgentWorkflow.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if binding is None:
            raise NotFoundError("员工工作流关联")
        if binding.enabled:
            raise ConflictError("请先停用此工作流关联，再固定旧版本")
        if all((binding.release_version, binding.release_id, binding.release_digest)):
            raise ConflictError("该员工工作流已有固定发布版本")

        releases = await self.resolver.resolve_published_bindings(
            [workflow_id], str(project), {workflow_id: command.release_version}
        )
        release = releases.get(workflow_id)
        if (
            release is None
            or release.workflow_id != workflow_id
            or release.release_version != command.release_version
            or not release.release_id
            or not release.definition_digest
        ):
            raise ConflictError("目标工作流发布版本无法核验")

        old_version, old_id, old_digest = (
            binding.release_version,
            binding.release_id,
            binding.release_digest,
        )
        binding.release_version = release.release_version
        binding.release_id = release.release_id
        binding.release_digest = release.definition_digest
        transition = AgentWorkflowPinTransition(
            project_id=project,
            requested_by=actor,
            request_id=command.request_id,
            agent_id=agent_id,
            binding_id=binding.id,
            workflow_id=workflow_id,
            old_release_version=old_version,
            old_release_id=old_id,
            old_release_digest=old_digest,
            release_version=release.release_version,
            release_id=release.release_id,
            release_digest=release.definition_digest,
            agent_active=agent.is_active,
            binding_enabled=False,
        )
        self.db.add(transition)
        await self.db.flush()
        return WorkflowPinTransitionReceipt.model_validate(transition)
