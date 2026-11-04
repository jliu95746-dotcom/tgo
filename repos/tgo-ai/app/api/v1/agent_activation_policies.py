"""API-only management of explicit employee role/channel policies."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agent_trials import PrivateTrialRoute, TrialPrincipal, require_trial_principal
from app.database import get_db
from app.schemas.agent_activation import AgentActivationPolicies, AgentActivationPolicyChange, AgentActivationPolicyRecord
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_activation_policies import AgentActivationPolicyService
from app.schemas.agent_activation_readiness import AgentActivationReadiness
from app.services.agent_activation_readiness import AgentActivationReadinessService

router = APIRouter(route_class=PrivateTrialRoute)


def get_service(db: AsyncSession = Depends(get_db)) -> AgentActivationPolicyService:
    return AgentActivationPolicyService(db)


def get_readiness_service(db: AsyncSession = Depends(get_db)) -> AgentActivationReadinessService:
    return AgentActivationReadinessService(db)


@router.get("/{agent_id}/activation-readiness", response_model=AgentActivationReadiness)
async def read_readiness(agent_id: UUID, principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentActivationReadinessService = Depends(get_readiness_service)) -> AgentActivationReadiness:
    return await service.read(principal.project_id, principal.staff_id, agent_id)


@router.get("/{agent_id}/activation-policies", response_model=AgentActivationPolicies)
async def list_policies(agent_id: UUID, principal: TrialPrincipal = Depends(require_trial_principal),
                        service: AgentActivationPolicyService = Depends(get_service)) -> AgentActivationPolicies:
    return await service.list(principal.project_id, agent_id)


@router.put("/{agent_id}/activation-policies/{channel}", response_model=AgentActivationPolicyRecord)
async def save_policy(agent_id: UUID, channel: KnowledgeChannel, payload: AgentActivationPolicyChange,
                      principal: TrialPrincipal = Depends(require_trial_principal),
                      service: AgentActivationPolicyService = Depends(get_service)) -> AgentActivationPolicyRecord:
    if channel != payload.selection.channel:
        raise HTTPException(422, "请求渠道与岗位配置不一致")
    return await service.save(principal.project_id, principal.staff_id, agent_id, payload)


@router.delete("/{agent_id}/activation-policies/{channel}", response_model=AgentActivationPolicyRecord)
async def remove_policy(agent_id: UUID, channel: KnowledgeChannel, expected_revision: int = Query(ge=1),
                        principal: TrialPrincipal = Depends(require_trial_principal),
                        service: AgentActivationPolicyService = Depends(get_service)) -> AgentActivationPolicyRecord:
    return await service.remove(principal.project_id, principal.staff_id, agent_id, channel, expected_revision)
