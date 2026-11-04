"""Atomic materialization under the existing account mutation lock."""
from uuid import UUID
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, NotFoundError
from app.models.agent import Agent, AgentToolAssociation
from app.models.collection import AgentCollection
from app.models.workflow import AgentWorkflow
from app.models.agent_draft_binding import AgentDraftBinding
from app.schemas.agent import AgentBase, AgentCreate, AgentToolCreate, AgentUpdate
from app.schemas.agent_drafts import AgentDraftReceipt, AgentDraftRequest
from app.services.agent_activation import lock_account_agents
from app.services.agent_service import AgentService
from app.services.agent_trial_fingerprint import private_digest, json_object
from app.services.agent_activation_policies import AgentActivationPolicyService


class AgentDraftService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.policies = AgentActivationPolicyService(db)

    async def _binding(self, project: UUID, draft: UUID) -> AgentDraftBinding | None:
        return cast(AgentDraftBinding | None, await self.db.scalar(select(AgentDraftBinding).where(
            AgentDraftBinding.project_id == project, AgentDraftBinding.draft_id == draft,
        ).execution_options(populate_existing=True)))

    async def get(self, project: UUID, staff: UUID, draft: UUID) -> AgentDraftReceipt:
        row = await self._binding(project, draft)
        if row is None or row.created_by != staff:
            raise NotFoundError("配置员工")
        return await self._receipt(row)

    async def _receipt(self, row: AgentDraftBinding) -> AgentDraftReceipt:
        # Keep tombstones: deleting the saved employee must not create another.
        agent = await self.db.scalar(select(Agent).where(
            Agent.id == row.agent_id, Agent.project_id == row.project_id,
            Agent.deleted_at.is_(None),
        ).execution_options(populate_existing=True))
        if agent is None:
            raise ConflictError("此草稿关联的员工已删除，请建立新的配置草稿")
        return AgentDraftReceipt(
            project_id=row.project_id, created_by=row.created_by, draft_id=row.draft_id,
            revision=row.revision, agent_id=agent.id, is_active=agent.is_active,
            activation_selection=await self.policies.draft_selection(
                row.project_id, row.agent_id, row.draft_id, row.revision),
        )

    @staticmethod
    def _digest(request: AgentDraftRequest) -> str:
        agent = request.agent.model_copy(deep=True)
        agent.tools = sorted(agent.tools or [], key=lambda item: str(item.tool_id))
        agent.collections = sorted(agent.collections or [])
        agent.workflows = sorted(agent.workflows or [])
        snapshot = request.model_copy(update={"agent": agent})
        # Preserve digests of legacy requests that had no selection field.
        omitted = {"activation_selection"} if snapshot.activation_selection is None else set()
        return private_digest(json_object.validate_python(snapshot.model_dump(mode="json", exclude=omitted)))

    async def _configuration_matches(self, row: AgentDraftBinding) -> bool:
        agent = await self.db.get(Agent, row.agent_id, populate_existing=True)
        if agent is None:
            return False
        fields: dict[str, object] = {key: getattr(agent, key) for key in AgentBase.model_fields}
        # Activation state is independent; same-revision retries never toggle it.
        fields.update(is_active=False, is_default=False, llm_provider_id=agent.llm_provider_id)
        tools = (await self.db.scalars(select(AgentToolAssociation).where(
            AgentToolAssociation.agent_id == agent.id, AgentToolAssociation.deleted_at.is_(None),
        ))).all()
        fields["tools"] = [AgentToolCreate(tool_id=tool.tool_id, enabled=tool.enabled,
            permissions=tool.permissions, config=tool.config) for tool in tools]
        fields["collections"] = list((await self.db.scalars(select(AgentCollection.collection_id).where(
            AgentCollection.agent_id == agent.id, AgentCollection.deleted_at.is_(None),
        ))).all())
        fields["workflows"] = list((await self.db.scalars(select(AgentWorkflow.workflow_id).where(
            AgentWorkflow.agent_id == agent.id, AgentWorkflow.deleted_at.is_(None),
        ))).all())
        return self._digest(AgentDraftRequest(revision=row.revision,
            agent=AgentCreate.model_validate(fields),
            activation_selection=await self.policies.draft_selection(
                row.project_id, row.agent_id, row.draft_id, row.revision))) == row.request_digest

    async def save(self, project: UUID, staff: UUID, draft: UUID,
                   request: AgentDraftRequest) -> AgentDraftReceipt:
        digest = self._digest(request)
        await lock_account_agents(self.db, project)
        row = await self._binding(project, draft)
        if row is not None:
            if row.created_by != staff:
                raise NotFoundError("配置员工")
            if request.revision < row.revision:
                raise ConflictError("员工已经保存较新的配置，请重新读取草稿")
            if request.revision == row.revision:
                if digest != row.request_digest:
                    raise ConflictError("同一草稿版本的配置不一致")
                if not await self._configuration_matches(row):
                    raise ConflictError("员工已在其他页面修改，请重新读取并保存配置草稿")
                return await self._receipt(row)
            receipt = await self._receipt(row)
            if receipt.is_active:
                raise ConflictError("员工已经启用，请先停用再修改配置草稿")
        service = AgentService(self.db)
        try:
            if row is None:
                # Validate every persisted association, including creation.
                if request.agent.collections:
                    await service._validate_collections_belong_to_project(request.agent.collections, project)
                if request.agent.workflows:
                    await service._validate_workflows_belong_to_project(request.agent.workflows, project)
                agent = await service.create_agent(project, request.agent, commit=False)
                row = AgentDraftBinding(project_id=project, created_by=staff, draft_id=draft,
                    agent_id=agent.id, revision=request.revision, request_digest=digest)
                self.db.add(row)
            else:
                fields = request.agent.model_dump(exclude={
                    "agent_category", "is_remote_store_agent", "remote_agent_url", "store_agent_id",
                })
                await service.update_agent(project, row.agent_id, AgentUpdate.model_validate(fields),
                                           commit=False, enrich_resources=False)
                row.revision, row.request_digest = request.revision, digest
            await self.policies.save_for_draft(project, staff, row.agent_id, draft,
                                                request.revision, request.activation_selection)
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            raise
        return await self.get(project, staff, draft)
