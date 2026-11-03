"""Recheck a managed employee for the server-authenticated execution channel."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.exceptions import AuthorizationError, ConflictError
from app.models.agent_activation_policy import AgentActivationPolicy
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.internal import Agent
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_activation_guard import business_fingerprint
from app.services.agent_activation_readiness import AgentActivationReadinessService
from app.services.agent_trial_fingerprint import configuration_fingerprint
from app.services.agent_business_evidence_revision import business_evidence_revision

RuntimeReader = Callable[[UUID, UUID, UUID], Awaitable[Agent]]


class AgentExecutionGuard:
    def __init__(self, db: AsyncSession, *, readiness: AgentActivationReadinessService | None = None,
                 runtime_reader: RuntimeReader | None = None):
        self.db = db
        self.readiness = readiness or AgentActivationReadinessService(db)
        self.runtime_reader = runtime_reader or self._runtime

    async def _runtime(self, project: UUID, actor: UUID, agent_id: UUID) -> Agent:
        # Keep imports deferred: the runtime adapter also owns legacy conversion.
        from app.runtime.supervisor.infrastructure.services import AIServiceClient
        from app.services.agent_service import AgentService
        async with AIServiceClient(AgentService(self.db), project) as reader:
            return await reader.get_trial_agent(agent_id, requested_by=actor, verify_runtime=True)

    @staticmethod
    def _identity(row: AgentActivationPolicy) -> tuple[object, ...]:
        return (row.id, row.updated_by, row.channel, row.template_id, row.template_version,
                row.revision, row.requirements_revision, row.deleted_at)

    async def prepare(self, project: UUID, loaded: Agent, channel: KnowledgeChannel | None) -> Agent:
        try:
            async with asyncio.timeout(20):
                return await self._prepare(project, loaded, channel)
        except TimeoutError:
            raise ConflictError("当前员工能力核验超时，请稍后重试或转人工") from None

    async def _prepare(self, project: UUID, loaded: Agent, channel: KnowledgeChannel | None) -> Agent:
        policies_service = self.readiness.policies
        rows = await policies_service._rows(project, loaded.id)
        stored = (
            await policies_service._agent(project, loaded.id) if rows else None
        )
        active = (
            stored.is_active if stored is not None
            else await policies_service.is_agent_active(project, loaded.id)
        )
        if not active or loaded.project_id != str(project):
            raise AuthorizationError("员工已停用或执行范围无法确认")
        if stored is None:
            business = await self.db.scalar(select(AgentBusinessConnection.id).where(
                AgentBusinessConnection.project_id == project,
                AgentBusinessConnection.agent_id == loaded.id,
                AgentBusinessConnection.enabled.is_(True),
                AgentBusinessConnection.deleted_at.is_(None),
            ).limit(1))
            if business:
                raise AuthorizationError("业务连接需要先配置岗位和服务渠道，并完成正式执行核验")
            # Pre-policy employees retain their existing runtime constraints.
            # Any retained policy history, including removal, takes the checked path.
            return loaded
        if channel is None:
            raise AuthorizationError("员工执行渠道必须由业务服务确认")
        policies = [row for row in rows if row.channel == channel.value and row.deleted_at is None]
        if len(policies) != 1:
            raise AuthorizationError("当前渠道尚未配置和验证该员工的业务能力")
        policy = policies[0]
        identity, actor = self._identity(policy), policy.updated_by
        business_revision = await business_fingerprint(self.db, stored)
        proof = await self.readiness.read(project, actor, loaded.id, channel=channel)
        if (proof.project_id != project or proof.agent_id != loaded.id or proof.checked_by != actor
            or proof.scope != "employee_activation" or not proof.enabled or not proof.can_enable
            or len(proof.channels) != 1 or proof.channels[0].channel != channel
            or proof.checked_at.tzinfo is None
            or not -30 <= (datetime.now(UTC) - proof.checked_at).total_seconds() <= 60):
            raise ConflictError("当前渠道的员工配置或测试已失效，请补齐配置、重测或转人工")
        current = await self.runtime_reader(project, actor, loaded.id)
        if (current.id != loaded.id or current.project_id != str(project)
            or not current.model_owner_revision
            or configuration_fingerprint(current) != proof.configuration_fingerprint):
            raise ConflictError("员工或模型配置在核验期间发生变化，请重新核验")
        requirements = await self.readiness.policies._requirements(
            project, actor, self.readiness.policies._selection(policy),
        )
        if requirements.requirements_revision != proof.channels[0].requirements_revision:
            raise ConflictError("当前岗位要求或权限已变化，请重新核验")
        latest = await self.readiness.policies._agent(project, loaded.id)
        latest_rows = await self.readiness.policies._rows(project, loaded.id)
        if (not latest.is_active or not any(self._identity(row) == identity for row in latest_rows)
            or await business_fingerprint(self.db, latest) != business_revision
            or await business_evidence_revision(self.db, project, actor, loaded.id) != proof.business_evidence_revision):
            raise ConflictError("员工配置或当前渠道授权已变化，请重新核验")
        expiry = proof.channels[0].valid_until
        if expiry is None or expiry.tzinfo is None or expiry <= datetime.now(UTC):
            raise ConflictError("员工测试已到期，请重新测试和评审")
        return current
