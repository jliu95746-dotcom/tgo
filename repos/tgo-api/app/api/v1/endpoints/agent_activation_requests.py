"""Login-scoped activation request submission and original-result recovery."""
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.agent_configuration_access import (
    require_agent_configuration_actor,
)
from app.models.staff import Staff
from app.schemas.agent_activation_requests import (
    AgentActivationReceipt,
    AgentActivationRequest,
)
from app.services.ai_client import ai_client

router = APIRouter()


@router.post(
    "/{agent_id}/activation-requests", response_model=AgentActivationReceipt
)
async def submit(
    agent_id: UUID,
    command: AgentActivationRequest,
    actor: Staff = Depends(require_agent_configuration_actor),
) -> AgentActivationReceipt:
    return await ai_client.submit_agent_activation_request(
        actor.project_id, actor.id, agent_id, command
    )


@router.get(
    "/{agent_id}/activation-requests/{request_key}",
    response_model=AgentActivationReceipt,
)
async def lookup(
    agent_id: UUID,
    request_key: UUID,
    actor: Staff = Depends(require_agent_configuration_actor),
) -> AgentActivationReceipt:
    return await ai_client.lookup_agent_activation_request(
        actor.project_id, actor.id, agent_id, request_key
    )
