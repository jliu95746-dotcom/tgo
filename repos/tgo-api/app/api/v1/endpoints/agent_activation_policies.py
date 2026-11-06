"""Administrators configure role/channel requirements using login identity."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.security import require_admin
from app.api.company_configuration_access import require_configuration_write
from app.models.staff import Staff
from app.schemas.agent_activation import (
    AgentActivationPolicies,
    AgentActivationPolicyChange,
    AgentActivationPolicyRecord,
)
from app.schemas.knowledge import KnowledgeChannel
from app.services.ai_client import ai_client
from app.schemas.agent_activation_readiness import AgentActivationReadiness

router = APIRouter(dependencies=[Depends(require_configuration_write)])


@router.get(
    "/{agent_id}/activation-readiness", response_model=AgentActivationReadiness
)
async def read_readiness(
    agent_id: UUID, actor: Staff = Depends(require_admin())
) -> AgentActivationReadiness:
    return await ai_client.agent_activation_readiness(
        actor.project_id, actor.id, agent_id
    )


@router.get(
    "/{agent_id}/activation-policies", response_model=AgentActivationPolicies
)
async def list_policies(
    agent_id: UUID, actor: Staff = Depends(require_admin())
) -> AgentActivationPolicies:
    return await ai_client.agent_activation_policies(
        actor.project_id, actor.id, agent_id
    )


@router.put(
    "/{agent_id}/activation-policies/{channel}",
    response_model=AgentActivationPolicyRecord,
)
async def save_policy(
    agent_id: UUID,
    channel: KnowledgeChannel,
    payload: AgentActivationPolicyChange,
    actor: Staff = Depends(require_admin()),
) -> AgentActivationPolicyRecord:
    return await ai_client.save_agent_activation_policy(
        actor.project_id, actor.id, agent_id, channel, payload
    )


@router.delete(
    "/{agent_id}/activation-policies/{channel}",
    response_model=AgentActivationPolicyRecord,
)
async def remove_policy(
    agent_id: UUID,
    channel: KnowledgeChannel,
    expected_revision: int = Query(ge=1),
    actor: Staff = Depends(require_admin()),
) -> AgentActivationPolicyRecord:
    return await ai_client.remove_agent_activation_policy(
        actor.project_id, actor.id, agent_id, channel, expected_revision
    )
