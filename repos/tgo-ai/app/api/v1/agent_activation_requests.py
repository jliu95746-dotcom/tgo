"""Private request recovery; a historical receipt never authorizes another switch."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agent_trials import (
    PrivateTrialRoute,
    TrialPrincipal,
    require_trial_principal,
)
from app.database import get_db
from app.schemas.agent_activation_requests import (
    AgentActivationReceipt,
    AgentActivationRequest,
)
from app.services.agent_activation_requests import (
    AgentActivationRequestService,
)

router = APIRouter(route_class=PrivateTrialRoute)


def get_service(
    db: AsyncSession = Depends(get_db),
) -> AgentActivationRequestService:
    return AgentActivationRequestService(db)


@router.post(
    "/{agent_id}/activation-requests", response_model=AgentActivationReceipt
)
async def submit(
    agent_id: UUID,
    command: AgentActivationRequest,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentActivationRequestService = Depends(get_service),
) -> AgentActivationReceipt:
    return await service.submit(
        principal.project_id, principal.staff_id, agent_id, command
    )


@router.get(
    "/{agent_id}/activation-requests/{request_key}",
    response_model=AgentActivationReceipt,
)
async def lookup(
    agent_id: UUID,
    request_key: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentActivationRequestService = Depends(get_service),
) -> AgentActivationReceipt:
    return await service.lookup(
        principal.project_id, principal.staff_id, agent_id, request_key
    )
