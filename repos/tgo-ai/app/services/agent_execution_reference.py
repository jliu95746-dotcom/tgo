"""Read the current owner state without activating or modifying an employee."""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.exceptions import AuthorizationError, ConflictError
from app.models.agent_activation_policy import AgentActivationPolicy
from app.models.internal import Agent
from app.schemas.agent_execution import AgentExecutionReference
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_activation_policies import AgentActivationPolicyService
from app.services.agent_execution_guard import AgentExecutionGuard
from app.services.agent_trial_fingerprint import (
    configuration_fingerprint,
    private_digest,
)
from app.services.agent_business_evidence_revision import (
    business_evidence_revision,
)
from app.services.agent_execution_scope import current_execution_scope


def policy_revision(
    rows: list[AgentActivationPolicy], channel: KnowledgeChannel | None
) -> str:
    return private_digest(
        {
            "managed": bool(rows),
            "policies": [
                {
                    "id": str(row.id),
                    "actor": str(row.updated_by),
                    "channel": row.channel,
                    "template": row.template_id,
                    "version": row.template_version,
                    "revision": row.revision,
                    "requirements": row.requirements_revision,
                    "removed": row.deleted_at.isoformat()
                    if row.deleted_at is not None
                    else None,
                }
                for row in rows
                if channel is None or row.channel == channel.value
            ],
        }
    )


async def reference_for(
    db: AsyncSession,
    project: UUID,
    agent: Agent,
    channel: KnowledgeChannel | None,
) -> AgentExecutionReference:
    rows = await AgentActivationPolicyService(db)._rows(project, agent.id)
    selected = [
        row
        for row in rows
        if row.deleted_at is None
        and channel is not None
        and row.channel == channel.value
    ]
    revision = None
    if len(selected) == 1:
        revision = await business_evidence_revision(
            db, project, selected[0].updated_by, agent.id
        )
    return AgentExecutionReference(
        project_id=project,
        agent_id=agent.id,
        channel=channel,
        configuration_fingerprint=configuration_fingerprint(agent),
        policy_revision=policy_revision(rows, channel),
        business_evidence_revision=revision,
    )


class AgentExecutionReferenceReader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def __call__(
        self, expected: AgentExecutionReference
    ) -> AgentExecutionReference:
        # Each action/publication owns a new read transaction, not an old
        # identity map.
        scope = current_execution_scope()
        observation = (
            expected
            if scope is not None and scope.reference == expected
            else None
        )
        async with self.session_factory() as db:
            return await read_reference(
                db,
                expected.project_id,
                expected.agent_id,
                expected.channel,
                observation=observation,
            )


async def read_reference(
    db: AsyncSession,
    project: UUID,
    agent_id: UUID,
    channel: KnowledgeChannel | None,
    *,
    observation: AgentExecutionReference | None = None,
) -> AgentExecutionReference:
    from app.runtime.supervisor.infrastructure.services import AIServiceClient
    from app.services.agent_service import AgentService

    guard = AgentExecutionGuard(db)
    if not await guard.readiness.policies.is_agent_active(project, agent_id):
        raise AuthorizationError("员工已停用，本次答复暂停发送")
    policies = await guard.readiness.policies._rows(project, agent_id)
    before = policy_revision(policies, channel)
    selected = [
        row
        for row in policies
        if row.deleted_at is None
        and channel is not None
        and row.channel == channel.value
    ]
    business_before = (
        await business_evidence_revision(
            db, project, selected[0].updated_by, agent_id
        )
        if len(selected) == 1
        else None
    )
    async with AIServiceClient(AgentService(db), project) as reader:
        loaded = await reader.get_agent(str(agent_id), {})
    current = await guard.prepare(
        project, loaded, channel, observation=observation
    )
    result = await reference_for(db, project, current, channel)
    if (
        result.policy_revision != before
        or result.business_evidence_revision != business_before
    ):
        raise ConflictError("员工岗位授权在执行核验期间已变化")
    return result
