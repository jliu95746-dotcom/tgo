"""Administrators configure paused employees using their current login scope."""
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.company_configuration_access import require_configuration_write
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.agent_business_connections import (
    AgentBusinessConnectionRecord,
    AgentBusinessConnections,
    AgentBusinessConnectionWrite,
)
from app.services.ai_client import ai_client

router = APIRouter(dependencies=[Depends(require_configuration_write)])


@router.get(
    "/{agent_id}/business-connections", response_model=AgentBusinessConnections
)
async def read(
    agent_id: UUID, actor: Staff = Depends(require_admin())
) -> AgentBusinessConnections:
    return await ai_client.agent_business_connections(
        actor.project_id, actor.id, agent_id
    )


@router.get(
    "/{agent_id}/business-connections/requests/{request_id}",
    response_model=AgentBusinessConnectionRecord,
)
async def recover(
    agent_id: UUID, request_id: UUID, actor: Staff = Depends(require_admin())
) -> AgentBusinessConnectionRecord:
    return await ai_client.agent_business_connection_request(
        actor.project_id, actor.id, agent_id, request_id
    )


@router.put(
    "/{agent_id}/business-connections/{connection_id}",
    response_model=AgentBusinessConnectionRecord,
)
async def save(
    agent_id: UUID,
    connection_id: UUID,
    command: AgentBusinessConnectionWrite,
    actor: Staff = Depends(require_admin()),
) -> AgentBusinessConnectionRecord:
    return await ai_client.save_agent_business_connection(
        actor.project_id, actor.id, agent_id, connection_id, command
    )
