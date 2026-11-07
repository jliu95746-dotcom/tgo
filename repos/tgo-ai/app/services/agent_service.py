"""Agent service for business logic."""

import uuid
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.agent import Agent, AgentToolAssociation
from app.models.agent_device_script_binding import AgentDeviceScriptBinding
from app.models.collection import AgentCollection
from app.models.tool import Tool
from app.models.workflow import AgentWorkflow
from app.schemas.agent import AgentCreate, AgentUpdate
from app.schemas.agent_activation_requests import ActivationExpectation
from app.schemas.device_access import (
    AgentDeviceActionReadiness,
    AgentDeviceActionStatus,
)
from app.services.rag_service import rag_service_client
from app.services.workflow_service import (
    WorkflowIntegrationRelease,
    workflow_service_client,
)
from app.services.agent_workflow_enrichment import (
    enrich_agent_workflow_bindings,
)
from app.services.device_control_client import device_control_client
from app.services.agent_humanization import validate_humanization_binding
from app.services.agent_activation import (
    deactivate_account_peers,
    lock_account_agents,
)
from app.services.agent_activation_guard import (
    business_fingerprint,
    check_live_binding,
    check_new_employee,
    require_activation,
)


class AgentService:
    """Service for agent-related business logic."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def device_action_readiness(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> AgentDeviceActionReadiness:
        (
            "Recheck an employee's fixed file action before workflow "
            'selection.'
        )
        agent = await self.get_agent(
            project_id, agent_id, enrich_resources=False
        )
        device_id = agent.bound_device_id
        if not device_id:
            return AgentDeviceActionReadiness(
                agent_id=agent_id,
                status="not_bound",
            )
        try:
            parsed_device_id = uuid.UUID(device_id)
        except ValueError:
            return AgentDeviceActionReadiness(
                agent_id=agent_id,
                device_id=device_id,
                status="invalid_binding",
            )
        ready_digest: str | None = None
        try:
            policy = await device_control_client.validate_binding(
                project_id,
                device_id,
            )
        except NotFoundError:
            status: AgentDeviceActionStatus = "device_missing"
        except ValidationError:
            status = "needs_test"
        else:
            if policy is None:
                status = "legacy_unverified"
            elif (
                policy.project_id != project_id
                or policy.device_id != parsed_device_id
                or policy.digest != agent.bound_device_policy_digest
            ):
                status = "needs_rebind"
            else:
                status = "ready"
                ready_digest = policy.digest
        return AgentDeviceActionReadiness(
            agent_id=agent_id,
            device_id=device_id,
            status=status,
            policy_digest=ready_digest,
        )

    async def create_agent(
        self,
        project_id: uuid.UUID,
        agent_data: AgentCreate,
        *,
        commit: bool = True,
        requested_by: uuid.UUID | None = None,
    ) -> Agent:
        """
        Create a new agent.

        Args:
            project_id: Project ID
            agent_data: Agent creation data

        Returns:
            Created agent

        Raises:
            ValidationError: If tools not found
        """
        check_new_employee(agent_data)
        await validate_humanization_binding(
            project_id,
            agent_data.humanization_skill_name,
            agent_data.humanization_skill_enabled,
        )
        device_policy_digest: str | None = None
        if agent_data.bound_device_id is not None:
            read_policy = await device_control_client.validate_binding(
                project_id, agent_data.bound_device_id
            )
            device_policy_digest = read_policy.digest if read_policy else None
        # Validate tools if provided

        if agent_data.tools:
            tool_ids = [tool.tool_id for tool in agent_data.tools]
            await self._validate_tools_belong_to_project(tool_ids, project_id)
        if agent_data.collections:
            await self._validate_collections_belong_to_project(
                agent_data.collections, project_id
            )
        if len(agent_data.workflows or []) != len(
            set(agent_data.workflows or [])
        ):
            raise ValidationError("工作流关联不能重复", "workflows")
        if not set(agent_data.workflow_release_versions).issubset(
            set(agent_data.workflows or [])
        ):
            raise ValidationError(
                "指定版本的工作流必须已关联员工", "workflow_release_versions"
            )
        resolved_workflows = await self._validate_workflows_belong_to_project(
            agent_data.workflows or [],
            project_id,
            agent_data.workflow_release_versions,
        )

        await lock_account_agents(self.db, project_id)
        # Create agent

        agent = Agent(
            project_id=project_id,
            llm_provider_id=agent_data.llm_provider_id,
            name=agent_data.name,
            instruction=agent_data.instruction,
            model=agent_data.model,
            is_default=agent_data.is_active or agent_data.is_default,
            is_active=agent_data.is_active,
            is_remote_store_agent=agent_data.is_remote_store_agent,
            remote_agent_url=agent_data.remote_agent_url,
            store_agent_id=agent_data.store_agent_id,
            agent_category=agent_data.agent_category.value,
            config=agent_data.config,
            bound_device_id=agent_data.bound_device_id,
            bound_device_policy_digest=device_policy_digest,
            skills_enabled=agent_data.skills_enabled,
            humanization_skill_name=agent_data.humanization_skill_name,
            humanization_skill_enabled=agent_data.humanization_skill_enabled,
        )

        self.db.add(agent)
        await self.db.flush()  # Get agent ID

        # Create tool associations (many-to-many)

        if agent_data.tools:
            for tool_binding in agent_data.tools:
                association = AgentToolAssociation(
                    agent_id=agent.id,
                    tool_id=tool_binding.tool_id,
                    enabled=tool_binding.enabled,
                    permissions=tool_binding.permissions,
                    config=tool_binding.config,
                )
                self.db.add(association)
        # Create collection bindings

        if agent_data.collections:
            for collection_id_str in agent_data.collections:
                agent_collection = AgentCollection(
                    agent_id=agent.id,
                    collection_id=collection_id_str,
                )
                self.db.add(agent_collection)
        # Create workflow bindings

        if agent_data.workflows:
            for workflow_id_str in agent_data.workflows:
                release = resolved_workflows[workflow_id_str]
                agent_workflow = AgentWorkflow(
                    agent_id=agent.id,
                    workflow_id=workflow_id_str,
                    release_version=release.release_version,
                    release_id=release.release_id,
                    release_digest=release.definition_digest,
                )
                self.db.add(agent_workflow)
        if commit:
            await self.db.commit()
            await self.db.refresh(agent)
        else:
            await self.db.flush()
        return agent

    async def get_agent(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        enrich_resources: bool = True,
    ) -> Agent:
        """
        Get an agent by ID.

        Args:
            project_id: Project ID
            agent_id: Agent ID

        Returns:
            Agent

        Raises:
            NotFoundError: If agent not found
        """
        stmt = (
            select(Agent)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.collections),
                selectinload(Agent.workflows),
                selectinload(Agent.llm_provider),
            )
            .where(
                and_(
                    Agent.id == agent_id,
                    Agent.project_id == project_id,
                    Agent.deleted_at.is_(None),
                )
            )
        )
        result = await self.db.execute(
            stmt.execution_options(populate_existing=True)
        )
        agent = result.scalar_one_or_none()

        if not agent:
            raise NotFoundError("Agent", agent_id)
        if not enrich_resources:
            return agent
        # API responses need display details from downstream services. Runtime
        # execution only needs the persisted bindings already loaded above.

        enriched_agents = await self.enrich_agents_with_collection_data(
            [agent], project_id
        )
        enriched_agents = await self.enrich_agents_with_workflow_data(
            enriched_agents, project_id
        )
        enriched_agents = await self.enrich_agents_with_tool_details(
            enriched_agents, project_id
        )
        return enriched_agents[0]

    async def list_agents(
        self,
        project_id: uuid.UUID,
        model: Optional[str] = None,
        is_default: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0,
        *,
        bound_device_id: uuid.UUID | None = None,
        enrich_resources: bool = True,
    ) -> Tuple[List[Agent], int]:
        """
        List agents for a project.

        Args:
            project_id: Project ID
            model: Filter by model
            is_default: Filter by default status
            limit: Number of agents to return
            offset: Number of agents to skip

        Returns:
            Tuple of (agents, total_count)
        """
        # Build base query

        conditions = [
            Agent.project_id == project_id,
            Agent.deleted_at.is_(None),
        ]

        if model is not None:
            conditions.append(Agent.model == model)
        if bound_device_id is not None:
            conditions.append(Agent.bound_device_id == str(bound_device_id))
        if is_default is not None:
            # One enabled employee serves the account. Older accounts can
            # still have a paused employee marked as their stored default.
            # Keep that fallback visible only when every employee is paused.

            enabled_exists = (
                select(Agent.id)
                .where(
                    Agent.project_id == project_id,
                    Agent.deleted_at.is_(None),
                    Agent.is_active.is_(True),
                )
                .correlate(None)
                .exists()
            )
            effective_default = or_(
                Agent.is_active.is_(True),
                and_(Agent.is_default.is_(True), ~enabled_exists),
            )
            conditions.append(
                effective_default if is_default else ~effective_default
            )
        # Get total count

        count_stmt = select(func.count(Agent.id)).where(and_(*conditions))
        count_result = await self.db.execute(count_stmt)
        total_count = int(count_result.scalar_one())

        # Get agents

        stmt = (
            select(Agent)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.collections),
                selectinload(Agent.workflows),
                selectinload(Agent.llm_provider),
            )
            .where(and_(*conditions))
            .order_by(Agent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        agents = result.scalars().all()

        if not enrich_resources:
            return list(agents), total_count
        # Enrich agents with collection data, workflow data, and tool details

        enriched_agents = await self.enrich_agents_with_collection_data(
            list(agents), project_id
        )
        enriched_agents = await self.enrich_agents_with_workflow_data(
            enriched_agents, project_id
        )
        enriched_agents = await self.enrich_agents_with_tool_details(
            enriched_agents, project_id
        )
        return enriched_agents, total_count

    async def update_agent(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        agent_data: AgentUpdate,
        *,
        commit: bool = True,
        enrich_resources: bool = True,
        requested_by: uuid.UUID | None = None,
        expected_activation: "ActivationExpectation | None" = None,
    ) -> Agent:
        """
        Update an agent.

        Args:
            project_id: Project ID
            agent_id: Agent ID
            agent_data: Agent update data

        Returns:
            Updated agent

        Raises:
            NotFoundError: If agent not found
        """
        # A savepoint also protects callers that own the enclosing transaction.

        async with self.db.begin_nested():
            agent = await self._update_agent(
                project_id,
                agent_id,
                agent_data,
                requested_by=requested_by,
                enrich_resources=enrich_resources,
                expected_activation=expected_activation,
            )
        if commit:
            await self.db.commit()
            await self.db.refresh(agent)
        return agent

    async def _update_agent(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        agent_data: AgentUpdate,
        *,
        requested_by: uuid.UUID | None,
        enrich_resources: bool,
        expected_activation: "ActivationExpectation | None" = None,
    ) -> Agent:
        if agent_data.is_active is False and agent_data.is_default is True:
            raise ValidationError("不能同时停用员工并将其切换为默认接待员工")
        await lock_account_agents(self.db, project_id)
        # Get existing agent

        agent = (
            await self.get_agent(project_id, agent_id)
            if enrich_resources
            else await self.get_agent(
                project_id,
                agent_id,
                enrich_resources=False,
            )
        )
        if {
            "humanization_skill_name",
            "humanization_skill_enabled",
        } & agent_data.model_fields_set:
            await validate_humanization_binding(
                project_id,
                agent_data.humanization_skill_name
                if "humanization_skill_name" in agent_data.model_fields_set
                else agent.humanization_skill_name,
                agent_data.humanization_skill_enabled
                if "humanization_skill_enabled" in agent_data.model_fields_set
                else agent.humanization_skill_enabled,
            )
        device_policy_digest: str | None = None
        if "bound_device_id" in agent_data.model_fields_set:
            if agent_data.bound_device_id is not None:
                script = await self.db.scalar(
                    select(AgentDeviceScriptBinding.id)
                    .where(
                        AgentDeviceScriptBinding.project_id == project_id,
                        AgentDeviceScriptBinding.agent_id == agent_id,
                        AgentDeviceScriptBinding.enabled.is_(True),
                        AgentDeviceScriptBinding.deleted_at.is_(None),
                    )
                    .limit(1)
                )
                if script is not None:
                    raise ConflictError("请先解除员工脚本绑定，再配置设备读取")
                read_policy = await device_control_client.validate_binding(
                    project_id, agent_data.bound_device_id
                )
                device_policy_digest = (
                    read_policy.digest if read_policy else None
                )
                if (
                    agent.bound_device_id == agent_data.bound_device_id
                    and agent.bound_device_policy_digest
                    != device_policy_digest
                ):
                    raise ConflictError("设备策略已变化，请先解除原设备绑定并重新选择设备")
        # Validate collections if provided

        if agent_data.collections is not None:
            await self._validate_collections_belong_to_project(
                agent_data.collections, project_id
            )
        # Preserve existing pins when the editor resends unchanged workflow
        # IDs.

        existing_workflows = {
            binding.workflow_id: binding
            for binding in agent.workflows
            if binding.deleted_at is None
        }
        wanted_workflow_ids = (
            agent_data.workflows
            if agent_data.workflows is not None
            else list(existing_workflows)
        )
        if len(wanted_workflow_ids) != len(set(wanted_workflow_ids)):
            raise ValidationError("工作流关联不能重复", "workflows")
        if not set(agent_data.workflow_release_versions).issubset(
            set(wanted_workflow_ids)
        ):
            raise ValidationError(
                "指定版本的工作流必须已关联员工", "workflow_release_versions"
            )
        ids_to_resolve = (
            wanted_workflow_ids
            if agent_data.refresh_workflow_releases
            else [
                item
                for item in wanted_workflow_ids
                if item not in existing_workflows
                or item in agent_data.workflow_release_versions
            ]
        )
        for workflow_id in ids_to_resolve:
            existing_binding = existing_workflows.get(workflow_id)
            if existing_binding is not None and not all(
                (
                    existing_binding.release_version,
                    existing_binding.release_id,
                    existing_binding.release_digest,
                )
            ):
                raise ConflictError("旧关联请先停用，再通过工作流版本切换入口固定发布版本")
        resolved_workflows: Dict[str, WorkflowIntegrationRelease] = {}
        if ids_to_resolve:
            resolved_workflows = (
                await self._validate_workflows_belong_to_project(
                    ids_to_resolve,
                    project_id,
                    {
                        item: agent_data.workflow_release_versions[item]
                        for item in ids_to_resolve
                        if item in agent_data.workflow_release_versions
                    },
                )
            )
        # Validate tools if provided

        if agent_data.tools is not None:
            tool_ids = [tool.tool_id for tool in agent_data.tools]
            await self._validate_tools_belong_to_project(tool_ids, project_id)
        before = await business_fingerprint(self.db, agent)
        wanted_active = (
            agent_data.is_active
            if agent_data.is_active is not None
            else agent.is_active
        )
        if agent_data.is_default is True:
            wanted_active = True
        # Update basic fields

        update_data = agent_data.model_dump(
            exclude_unset=True,
            exclude={
                "tools",
                "collections",
                "workflows",
                "workflow_release_versions",
                "is_active",
                "is_default",
                "refresh_workflow_releases",
            },
        )
        for field, value in update_data.items():
            setattr(agent, field, value)
        if "bound_device_id" in agent_data.model_fields_set:
            agent.bound_device_policy_digest = device_policy_digest
        # Update tools if provided

        if agent_data.tools is not None:
            # Delete existing tool associations

            stmt_delete = select(AgentToolAssociation).where(
                and_(
                    AgentToolAssociation.agent_id == agent.id,
                    AgentToolAssociation.deleted_at.is_(None),
                )
            )
            result = await self.db.execute(stmt_delete)
            existing_associations = result.scalars().all()
            for association in existing_associations:
                await self.db.delete(association)
            # Create new tool associations

            for tool_binding in agent_data.tools:
                association = AgentToolAssociation(
                    agent_id=agent.id,
                    tool_id=tool_binding.tool_id,
                    enabled=tool_binding.enabled,
                    permissions=tool_binding.permissions,
                    config=tool_binding.config,
                )
                self.db.add(association)
            await self.db.flush()
        # Update collections if provided

        if agent_data.collections is not None:
            # Remove existing collection associations

            for agent_collection in agent.collections:
                await self.db.delete(agent_collection)
            # Add new collection associations

            for collection_id_str in agent_data.collections:
                agent_collection = AgentCollection(
                    agent_id=agent.id,
                    collection_id=collection_id_str,
                )
                self.db.add(agent_collection)
        # Update workflows if provided

        if (
            agent_data.workflows is not None
            or agent_data.refresh_workflow_releases
            or agent_data.workflow_release_versions
        ):
            for workflow_id, binding in existing_workflows.items():
                if workflow_id not in wanted_workflow_ids:
                    await self.db.delete(binding)
            for workflow_id in wanted_workflow_ids:
                release = resolved_workflows.get(workflow_id)
                existing_binding = existing_workflows.get(workflow_id)
                if existing_binding is None:
                    if release is None:
                        raise ValidationError("新增工作流缺少发布版本", "workflows")
                    self.db.add(
                        AgentWorkflow(
                            agent_id=agent.id,
                            workflow_id=workflow_id,
                            release_version=release.release_version,
                            release_id=release.release_id,
                            release_digest=release.definition_digest,
                        )
                    )
                elif release is not None:
                    existing_binding.release_version = release.release_version
                    existing_binding.release_id = release.release_id
                    existing_binding.release_digest = release.definition_digest
        await self.db.flush()
        # Load replacement associations before comparing the candidate.

        agent = (
            await self.get_agent(project_id, agent_id)
            if enrich_resources
            else await self.get_agent(
                project_id,
                agent_id,
                enrich_resources=False,
            )
        )
        changed = before != await business_fingerprint(self.db, agent)
        requires_check = wanted_active and (
            agent_data.is_active is True
            or agent_data.is_default is True
            or changed
        )
        if requires_check:
            if expected_activation is None:
                await require_activation(
                    self.db, project_id, requested_by, agent_id
                )
            else:
                await require_activation(
                    self.db,
                    project_id,
                    requested_by,
                    agent_id,
                    expected=expected_activation,
                )
            # Flush candidate business changes while keeping old activation,
            # then retire peers before this row reaches the unique index.

            await deactivate_account_peers(self.db, project_id, agent.id)
            agent.is_default = True
        elif agent_data.is_default is not None:
            agent.is_default = agent_data.is_default
        agent.is_active = wanted_active
        await self.db.flush()
        return agent

    async def get_default_agent(
        self,
        project_id: uuid.UUID,
        *,
        enrich_resources: bool = True,
    ) -> Agent:
        """Get the current default agent for a project."""

        agents, _ = await self.list_agents(
            project_id=project_id,
            is_default=True,
            limit=1,
            offset=0,
            enrich_resources=enrich_resources,
        )
        if not agents:
            raise NotFoundError(
                "Default agent",
                details={"project_id": str(project_id)},
            )
        return agents[0]

    async def delete_agent(
        self, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        """
        Soft delete an agent.

        Args:
            project_id: Project ID
            agent_id: Agent ID

        Raises:
            NotFoundError: If agent not found
        """
        agent = await self.get_agent(project_id, agent_id)
        agent.soft_delete()
        await self.db.commit()

    async def set_tool_enabled(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tool_id: uuid.UUID,
        enabled: bool,
    ) -> None:
        """Enable or disable a specific tool binding for an agent."""
        await lock_account_agents(self.db, project_id)
        # Verify agent belongs to project

        stmt_agent = select(Agent).where(
            and_(
                Agent.id == agent_id,
                Agent.project_id == project_id,
                Agent.deleted_at.is_(None),
            )
        )
        res = await self.db.execute(
            stmt_agent.execution_options(populate_existing=True)
        )
        agent = res.scalar_one_or_none()
        if not agent:
            raise NotFoundError("Agent", agent_id)
        # Find association binding by agent and tool ID

        stmt_tool = select(AgentToolAssociation).where(
            and_(
                AgentToolAssociation.agent_id == agent_id,
                AgentToolAssociation.tool_id == tool_id,
                AgentToolAssociation.deleted_at.is_(None),
            )
        )
        res_tool = await self.db.execute(
            stmt_tool.execution_options(populate_existing=True)
        )
        binding = res_tool.scalar_one_or_none()
        if not binding:
            raise NotFoundError(
                "AgentToolAssociation", details={"tool_id": str(tool_id)}
            )
        check_live_binding(agent, binding.enabled, enabled)
        binding.enabled = enabled
        await self.db.commit()

    async def set_collection_enabled(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        collection_id: str,
        enabled: bool,
    ) -> None:
        """Enable or disable a specific collection binding for an agent."""
        await lock_account_agents(self.db, project_id)
        # Verify agent belongs to project

        stmt_agent = select(Agent).where(
            and_(
                Agent.id == agent_id,
                Agent.project_id == project_id,
                Agent.deleted_at.is_(None),
            )
        )
        res = await self.db.execute(
            stmt_agent.execution_options(populate_existing=True)
        )
        agent = res.scalar_one_or_none()
        if not agent:
            raise NotFoundError("Agent", agent_id)
        # Find binding

        stmt_col = select(AgentCollection).where(
            and_(
                AgentCollection.agent_id == agent_id,
                AgentCollection.collection_id == collection_id,
                AgentCollection.deleted_at.is_(None),
            )
        )
        res_col = await self.db.execute(
            stmt_col.execution_options(populate_existing=True)
        )
        binding = res_col.scalar_one_or_none()
        if not binding:
            raise NotFoundError(
                "AgentCollection", details={"collection_id": collection_id}
            )
        check_live_binding(agent, binding.enabled, enabled)
        binding.enabled = enabled
        await self.db.commit()

    async def set_workflow_enabled(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        workflow_id: str,
        enabled: bool,
    ) -> None:
        """Enable or disable a specific workflow binding for an agent."""
        await lock_account_agents(self.db, project_id)
        # Verify agent belongs to project

        stmt_agent = select(Agent).where(
            and_(
                Agent.id == agent_id,
                Agent.project_id == project_id,
                Agent.deleted_at.is_(None),
            )
        )
        res = await self.db.execute(
            stmt_agent.execution_options(populate_existing=True)
        )
        agent = res.scalar_one_or_none()
        if not agent:
            raise NotFoundError("Agent", agent_id)
        # Find binding

        stmt_wf = select(AgentWorkflow).where(
            and_(
                AgentWorkflow.agent_id == agent_id,
                AgentWorkflow.workflow_id == workflow_id,
                AgentWorkflow.deleted_at.is_(None),
            )
        )
        res_wf = await self.db.execute(
            stmt_wf.execution_options(populate_existing=True)
        )
        binding = res_wf.scalar_one_or_none()
        if not binding:
            raise NotFoundError(
                "AgentWorkflow", details={"workflow_id": workflow_id}
            )
        check_live_binding(agent, binding.enabled, enabled)
        if enabled and not all(
            (
                binding.release_version,
                binding.release_id,
                binding.release_digest,
            )
        ):
            raise ConflictError("工作流关联缺少固定发布版本，请先完成旧关联切换")
        binding.enabled = enabled
        await self.db.commit()

    async def clear_session_memory(
        self,
        session_id: str,
        project_id: uuid.UUID,
        user_id: Optional[str] = None,
    ) -> None:
        (
            'Clear all memory and session history for a specific session.\n'
            '\n'
            '        This deletes records from agno memory and session '
            "tables in the 'ai' schema.\n"
            '        If user_id is provided, it also clears personal '
            'memories for that user from agno_memories.\n'
            '        '
        )
        try:
            # Delete from agno_memories (user/agent memories)
            # The table may not exist if Agno hasn't created it yet

            if user_id:
                try:
                    await self.db.execute(
                        text(
                            (
                                'DELETE FROM ai.agno_memories WHERE user_id '
                                '= :user_id'
                            )
                        ),
                        {"user_id": user_id},
                    )
                except Exception:
                    pass
            # Delete from agno_sessions (session history)
            # The table may not exist if Agno hasn't created it yet

            try:
                await self.db.execute(
                    text(
                        (
                            'DELETE FROM ai.agno_sessions WHERE session_id = '
                            ':session_id'
                        )
                    ),
                    {"session_id": session_id},
                )
            except Exception:
                pass
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

    async def _replace_project_default_agent(
        self,
        project_id: uuid.UUID,
        new_default_agent_id: uuid.UUID,
        *,
        requested_by: uuid.UUID | None = None,
    ) -> None:
        (
            'Keep legacy internal default switching behind the same '
            'activation gate.'
        )
        await self.update_agent(
            project_id,
            new_default_agent_id,
            AgentUpdate(is_default=True),
            requested_by=requested_by,
            enrich_resources=False,
        )

    async def _clear_project_default_agents(
        self,
        project_id: uuid.UUID,
        exclude_agent_id: Optional[uuid.UUID] = None,
    ) -> None:
        (
            'Clear active default agents in a project before promoting '
            'another one.'
        )

        conditions = [
            Agent.project_id == project_id,
            Agent.deleted_at.is_(None),
            Agent.is_default.is_(True),
        ]
        if exclude_agent_id is not None:
            conditions.append(Agent.id != exclude_agent_id)
        await self.db.execute(
            update(Agent).where(and_(*conditions)).values(is_default=False)
        )

    async def _validate_collections_belong_to_project(
        self, collection_ids: List[str], project_id: uuid.UUID
    ) -> None:
        """
        Validate that all collections exist in the RAG service for the project.

        Args:
            collection_ids: List of collection ID strings
            project_id: Project ID

        Raises:
            NotFoundError: If any collection not found
            ValidationError: If validation fails
        """
        if not collection_ids:
            return
        # Validate collections exist in RAG service (trust project_id; no local
        # project validation)

        await rag_service_client.validate_collections_exist(
            collection_ids,
            str(project_id),
        )

    async def _validate_workflows_belong_to_project(
        self,
        workflow_ids: List[str],
        project_id: uuid.UUID,
        release_versions: Optional[Dict[str, int]] = None,
    ) -> Dict[str, WorkflowIntegrationRelease]:
        (
            '\n'
            '        Validate that all workflows exist in the Workflow '
            'service for the project.\n'
            '\n'
            '        Args:\n'
            '            workflow_ids: List of workflow ID strings\n'
            '            project_id: Project ID\n'
            '\n'
            '        Raises:\n'
            '            NotFoundError: If any workflow not found\n'
            '            ValidationError: If validation fails\n'
            '        '
        )
        if not workflow_ids:
            return {}
        # Validate workflows exist in Workflow service

        if release_versions:
            return await workflow_service_client.resolve_published_bindings(
                workflow_ids,
                str(project_id),
                release_versions,
            )
        return await workflow_service_client.resolve_published_bindings(
            workflow_ids,
            str(project_id),
        )

    async def _validate_tools_belong_to_project(
        self, tool_ids: List[uuid.UUID], project_id: uuid.UUID
    ) -> None:
        (
            '\n'
            '        Validate that all tools exist in the ai_tools table and '
            'belong to the project.\n'
            '\n'
            '        Args:\n'
            '            tool_ids: List of tool IDs\n'
            '            project_id: Project ID\n'
            '\n'
            '        Raises:\n'
            '            NotFoundError: If any tool not found\n'
            '            ValidationError: If any tool belongs to different '
            'project\n'
            '        '
        )
        if not tool_ids:
            return
        # Query tools from database

        stmt = select(Tool).where(
            and_(
                Tool.id.in_(tool_ids),
                Tool.deleted_at.is_(None),
            )
        )
        result = await self.db.execute(stmt)
        tools = result.scalars().all()

        # Check if all tools were found

        found_tool_ids = {tool.id for tool in tools}
        missing_tool_ids = set(tool_ids) - found_tool_ids
        if missing_tool_ids:
            raise NotFoundError(
                "Tool",
                details={
                    "missing_tool_ids": [str(tid) for tid in missing_tool_ids]
                },
            )
        # Check if all tools belong to the project

        wrong_project_tools = [
            tool for tool in tools if tool.project_id != project_id
        ]
        if wrong_project_tools:
            raise ValidationError(
                "Some tools belong to a different project",
                "tool_ids",
                {
                    "wrong_project_tool_ids": [
                        str(tool.id) for tool in wrong_project_tools
                    ],
                    "expected_project_id": str(project_id),
                },
            )

    async def enrich_agents_with_collection_data(
        self, agents: List[Agent], project_id: uuid.UUID
    ) -> List[Agent]:
        """
        Enrich agents with collection data from the RAG service.

        Args:
            agents: List of agents to enrich
            project_id: Project ID for RAG service authentication

        Returns:
            List of agents with collection data attached
        """
        if not agents:
            return agents
        # Skip local project existence check; rely on upstream services and use
        # project_id directly

        # Collect all unique collection IDs from all agents

        all_collection_ids = set()
        for agent in agents:
            for ac in agent.collections or []:
                all_collection_ids.add(ac.collection_id)
        if not all_collection_ids:
            # No collections to fetch

            for agent in agents:
                agent._collection_data = []
            return agents
        # Fetch collection data from RAG service

        try:
            batch_response = await rag_service_client.get_collections_batch(
                list(all_collection_ids),
                str(project_id),
            )

            # Create a mapping of collection_id -> collection_data

            collection_data_map = {
                str(collection.id): collection
                for collection in batch_response.collections
            }

            # Attach collection data (with enabled from binding) to each agent

            for agent in agents:
                agent_collection_data = []
                for ac in agent.collections or []:
                    cid = str(ac.collection_id)
                    if cid in collection_data_map:
                        col = collection_data_map[cid]
                        try:
                            col_with_enabled = col.model_copy(
                                update={
                                    "enabled": bool(
                                        getattr(ac, "enabled", True)
                                    )
                                }
                            )
                        except Exception:
                            col_with_enabled = col
                        agent_collection_data.append(col_with_enabled)
                agent._collection_data = agent_collection_data
        except Exception:
            # If RAG service fails, return agents without collection data

            for agent in agents:
                agent._collection_data = []
        return agents

    async def enrich_agents_with_workflow_data(
        self, agents: List[Agent], project_id: uuid.UUID
    ) -> List[Agent]:
        (
            'Preserve local bindings even if their remote details cannot be '
            'loaded.'
        )
        return await enrich_agent_workflow_bindings(agents, project_id)

    async def enrich_agents_with_tool_details(
        self, agents: List[Agent], project_id: uuid.UUID
    ) -> List[Agent]:
        (
            '\n'
            '        Enrich agents with tool details from local database '
            '(ai_tools table).\n'
            '\n'
            '        Flow:\n'
            '        - Load agent tool bindings (already present via '
            'selectinload in callers)\n'
            '        - Build AgentToolDetail objects from Tool entities\n'
            '        - Attach enabled state and other association fields '
            'from AgentToolAssociation\n'
            '        - Set agent._tools_data with enriched tool list\n'
            '        '
        )
        if not agents:
            return agents
        # Build association map: (agent_id, tool_id) -> (enabled, permissions,
        # config)

        assoc_stmt = select(
            AgentToolAssociation.agent_id,
            AgentToolAssociation.tool_id,
            AgentToolAssociation.enabled,
            AgentToolAssociation.permissions,
            AgentToolAssociation.config,
        ).where(
            and_(
                AgentToolAssociation.agent_id.in_([a.id for a in agents]),
                AgentToolAssociation.deleted_at.is_(None),
            )
        )
        assoc_res = await self.db.execute(assoc_stmt)
        assoc_map: Dict[
            Tuple[uuid.UUID, uuid.UUID],
            Tuple[bool, Optional[List[str]], Optional[dict]],
        ] = {
            (row[0], row[1]): (bool(row[2]), row[3], row[4])
            for row in assoc_res.all()
        }

        # Import AgentToolDetail here to avoid circular import

        from app.schemas.tool import AgentToolDetail

        # Attach per-agent tool details preserving original binding order

        for agent in agents:
            tool_details = []
            for tool_entity in agent.tools or []:
                if (
                    not tool_entity
                    or tool_entity.deleted_at is not None
                    or tool_entity.project_id != project_id
                    or agent.project_id != project_id
                ):
                    continue
                # Get association data

                assoc_data = assoc_map.get((agent.id, tool_entity.id))
                if assoc_data is None:
                    continue
                enabled, permissions, tool_config = assoc_data

                # Build AgentToolDetail from Tool entity with association
                # fields

                try:
                    # First convert Tool to dict

                    tool_dict = {
                        "id": tool_entity.id,
                        "project_id": tool_entity.project_id,
                        "name": tool_entity.name,
                        "description": tool_entity.description,
                        "tool_type": tool_entity.tool_type,
                        "transport_type": tool_entity.transport_type,
                        "endpoint": tool_entity.endpoint,
                        "config": tool_entity.config,
                        "created_at": tool_entity.created_at,
                        "updated_at": tool_entity.updated_at,
                        "deleted_at": tool_entity.deleted_at,
                        "enabled": enabled,
                        "permissions": permissions,
                        "tool_config": tool_config,
                    }

                    tool_detail = AgentToolDetail.model_validate(tool_dict)
                    tool_details.append(tool_detail)
                except Exception:
                    # Skip tools that fail validation

                    continue
            agent._tools_data = tool_details
        return agents
