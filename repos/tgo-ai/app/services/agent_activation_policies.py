"""Serialize explicit role/channel selections; never activate an employee."""

from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.models.agent import Agent
from app.models.agent_activation_policy import AgentActivationPolicy
from app.schemas.agent_activation import (
    AgentActivationPolicies,
    AgentActivationPolicyChange,
    AgentActivationPolicyRecord,
    AgentActivationRequirements,
    AgentActivationRequirementsQuery,
    AgentActivationSelection,
)
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.agent_configuration_actor import (
    AgentConfigurationActor,
    AgentConfigurationActorQuery,
)
from app.services.agent_activation import lock_account_agents
from app.services.api_service import api_service_client


class RequirementsOwner(Protocol):
    async def get_agent_configuration_actor(
        self, query: AgentConfigurationActorQuery
    ) -> AgentConfigurationActor:
        ...

    async def get_agent_activation_requirements(
        self, query: AgentActivationRequirementsQuery
    ) -> AgentActivationRequirements:
        ...


class AgentActivationPolicyService:
    def __init__(
        self, db: AsyncSession, owner: RequirementsOwner = api_service_client
    ):
        self.db, self.owner = db, owner

    async def is_agent_active(self, project: UUID, agent_id: UUID) -> bool:
        """Read the serving flag without loading employee relationships."""
        active = await self.db.scalar(
            select(Agent.is_active).where(
                Agent.id == agent_id,
                Agent.project_id == project,
                Agent.deleted_at.is_(None),
            )
        )
        if active is None:
            raise NotFoundError("员工")
        return active

    async def _agent(self, project: UUID, agent_id: UUID) -> Agent:
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
            raise NotFoundError("员工")
        return agent

    async def _rows(
        self, project: UUID, agent_id: UUID
    ) -> list[AgentActivationPolicy]:
        return list(
            (
                await self.db.scalars(
                    select(AgentActivationPolicy)
                    .where(
                        AgentActivationPolicy.project_id == project,
                        AgentActivationPolicy.agent_id == agent_id,
                    )
                    .order_by(AgentActivationPolicy.channel)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    @staticmethod
    def _selection(row: AgentActivationPolicy) -> AgentActivationSelection:
        return AgentActivationSelection.model_validate(
            {
                "template_id": row.template_id,
                "template_version": row.template_version,
                "channel": row.channel,
            }
        )

    @classmethod
    def _record(
        cls, row: AgentActivationPolicy
    ) -> AgentActivationPolicyRecord:
        return AgentActivationPolicyRecord(
            **cls._selection(row).model_dump(),
            id=row.id,
            revision=row.revision,
            requirements_revision=row.requirements_revision,
            configured_by=row.updated_by,
            enabled=row.deleted_at is None,
        )

    async def list(
        self, project: UUID, agent_id: UUID
    ) -> AgentActivationPolicies:
        await self._agent(project, agent_id)
        return AgentActivationPolicies(
            project_id=project,
            agent_id=agent_id,
            policies=[
                self._record(row)
                for row in await self._rows(project, agent_id)
            ],
        )

    async def _requirements(
        self,
        project: UUID,
        actor: UUID,
        selection: AgentActivationSelection,
        agent_id: UUID | None = None,
    ) -> AgentActivationRequirements:
        query = AgentActivationRequirementsQuery(
            **selection.model_dump(),
            project_id=project,
            requested_by=actor,
            agent_id=agent_id,
        )
        result = await self.owner.get_agent_activation_requirements(query)
        if any(
            getattr(result, key) != value
            for key, value in query.model_dump().items()
        ):
            raise ConflictError("当前岗位要求与员工配置不一致，请重新读取")
        return result

    async def _save(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        request: AgentActivationPolicyChange,
        source: UUID | None = None,
        source_revision: int | None = None,
        requirements: AgentActivationRequirements | None = None,
    ) -> AgentActivationPolicy:
        await self._agent(project, agent_id)
        requirements = requirements or await self._requirements(
            project, actor, request.selection, agent_id
        )
        await lock_account_agents(self.db, project)
        agent = await self._agent(project, agent_id)
        row = next(
            (
                value
                for value in await self._rows(project, agent_id)
                if value.channel == request.selection.channel.value
            ),
            None,
        )
        same = (
            row is not None
            and row.deleted_at is None
            and (
                self._selection(row) == request.selection
                and row.requirements_revision
                == requirements.requirements_revision
                and row.source_draft_id == source
                and row.source_draft_revision == source_revision
            )
        )
        if row is not None:
            if row.revision != request.expected_revision:
                if (
                    same
                    and row.updated_by == actor
                    and row.revision == request.expected_revision + 1
                ):
                    return row
                raise ConflictError("岗位配置已有新的版本，请重新读取")
            if same and row.updated_by == actor:
                return row
        elif request.expected_revision != 0:
            raise ConflictError("岗位配置不存在，请重新读取")
        if agent.is_active:
            raise ConflictError("请先停用员工再修改岗位和渠道配置")
        if row is None:
            row = AgentActivationPolicy(
                project_id=project,
                agent_id=agent_id,
                created_by=actor,
                revision=0,
            )
            self.db.add(row)
        row.template_id, row.template_version = (
            request.selection.template_id,
            request.selection.template_version,
        )
        row.channel = request.selection.channel.value
        row.requirements_revision, row.updated_by = (
            requirements.requirements_revision,
            actor,
        )
        row.source_draft_id, row.source_draft_revision = (
            source,
            source_revision,
        )
        row.deleted_at = None
        row.revision += 1
        await self.db.flush()
        return row

    async def save(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        request: AgentActivationPolicyChange,
    ) -> AgentActivationPolicyRecord:
        try:
            row = await self._save(project, actor, agent_id, request)
            receipt = self._record(row)
            await self.db.commit()
            return receipt
        except BaseException:
            await self.db.rollback()
            raise

    async def remove(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        channel: KnowledgeChannel,
        expected_revision: int,
    ) -> AgentActivationPolicyRecord:
        try:
            await self._agent(project, agent_id)
            await lock_account_agents(self.db, project)
            agent = await self._agent(project, agent_id)
            row = next(
                (
                    value
                    for value in await self._rows(project, agent_id)
                    if value.channel == channel.value
                ),
                None,
            )
            if row is None:
                raise NotFoundError("岗位配置")
            permission = await self.owner.get_agent_configuration_actor(
                AgentConfigurationActorQuery(
                    project_id=project, requested_by=actor
                )
            )
            if (
                permission.project_id != project
                or permission.requested_by != actor
            ):
                raise ExternalServiceError("tgo-api", "当前员工配置权限范围不一致")
            if row.revision != expected_revision:
                if not (
                    row.deleted_at is not None
                    and row.updated_by == actor
                    and row.revision == expected_revision + 1
                ):
                    raise ConflictError("岗位配置已有新的版本，请重新读取")
            elif row.deleted_at is None:
                if agent.is_active:
                    raise ConflictError("请先停用员工再移除岗位和渠道配置")
                row.soft_delete()
                row.updated_by = actor
                row.revision += 1
            receipt = self._record(row)
            await self.db.commit()
            return receipt
        except BaseException:
            await self.db.rollback()
            raise

    async def draft_selection(
        self, project: UUID, agent_id: UUID, draft: UUID, revision: int
    ) -> AgentActivationSelection | None:
        rows = await self._rows(project, agent_id)
        if not rows:
            return None
        enabled = [row for row in rows if row.deleted_at is None]
        if (
            len(enabled) != 1
            or enabled[0].source_draft_id != draft
            or enabled[0].source_draft_revision != revision
        ):
            raise ConflictError("岗位配置已在其他页面修改，请重新读取")
        return self._selection(enabled[0])

    async def save_for_draft(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        draft: UUID,
        revision: int,
        selection: AgentActivationSelection | None,
    ) -> None:
        """Flush in the caller's transaction; the draft owns commit/rollback."""
        await self._agent(project, agent_id)
        await lock_account_agents(self.db, project)
        rows = await self._rows(project, agent_id)
        if selection is None:
            if rows:
                raise ConflictError("保存草稿时不能移除已有岗位要求")
            return
        if any(
            row.source_draft_id != draft
            for row in rows
            if row.deleted_at is None
        ):
            raise ConflictError("岗位配置已在其他页面修改，请重新读取")
        owned = [
            row
            for row in rows
            if row.source_draft_id == draft and row.deleted_at is None
        ]
        previous = [row for row in rows if row.source_draft_id == draft]
        latest_revision = max(
            (row.source_draft_revision or 0 for row in previous), default=0
        )
        if latest_revision > revision:
            raise ConflictError("岗位草稿已有新的版本，请重新读取")
        if latest_revision == revision and (
            len(owned) != 1 or self._selection(owned[0]) != selection
        ):
            raise ConflictError("同一草稿版本不能改变或恢复岗位配置")
        requirements = await self._requirements(
            project, actor, selection, agent_id
        )
        target = next(
            (row for row in rows if row.channel == selection.channel.value),
            None,
        )
        if target is not None and target.source_draft_id != draft:
            raise ConflictError("此渠道配置已在其他页面修改，请重新读取")
        if (
            latest_revision == revision
            and owned[0].requirements_revision
            != requirements.requirements_revision
        ):
            raise ConflictError("岗位要求已有变化，请重新保存草稿")
        # Retire a former draft channel only after the new target can be saved.
        await self._save(
            project,
            actor,
            agent_id,
            AgentActivationPolicyChange(
                expected_revision=target.revision if target else 0,
                selection=selection,
            ),
            draft,
            revision,
            requirements,
        )
        for row in owned:
            if row.channel != selection.channel.value:
                row.soft_delete()
                row.updated_by = actor
                row.revision += 1
        await self.db.flush()
