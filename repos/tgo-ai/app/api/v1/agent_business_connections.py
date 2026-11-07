"""API-only business binding configuration for paused employees."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agent_trials import (
    PrivateTrialRoute,
    TrialPrincipal,
    require_trial_principal,
)
from app.database import get_db
from app.schemas.agent_business_connections import (
    AgentBusinessConnectionRecord,
    AgentBusinessConnections,
    AgentBusinessConnectionWrite,
)
from app.services.agent_business_connections import (
    AgentBusinessConnectionService,
)

router = APIRouter(route_class=PrivateTrialRoute)


def get_service(
    db: AsyncSession = Depends(get_db),
) -> AgentBusinessConnectionService:
    return AgentBusinessConnectionService(db)


@router.get(
    "/{agent_id}/business-connections", response_model=AgentBusinessConnections
)
async def read(
    agent_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessConnectionService = Depends(get_service),
) -> AgentBusinessConnections:
    return await service.list(
        principal.project_id, principal.staff_id, agent_id
    )


@router.get(
    "/{agent_id}/business-connections/requests/{request_id}",
    response_model=AgentBusinessConnectionRecord,
)
async def recover(
    agent_id: UUID,
    request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessConnectionService = Depends(get_service),
) -> AgentBusinessConnectionRecord:
    return await service.request_result(
        principal.project_id, principal.staff_id, agent_id, request_id
    )


@router.put(
    "/{agent_id}/business-connections/{connection_id}",
    response_model=AgentBusinessConnectionRecord,
)
async def save(
    agent_id: UUID,
    connection_id: UUID,
    command: AgentBusinessConnectionWrite,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessConnectionService = Depends(get_service),
) -> AgentBusinessConnectionRecord:
    return await service.save(
        principal.project_id,
        principal.staff_id,
        agent_id,
        connection_id,
        command,
    )
