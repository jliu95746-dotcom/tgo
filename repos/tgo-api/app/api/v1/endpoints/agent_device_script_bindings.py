"""Current administrators configure paused employee script bindings."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.company_configuration_access import require_configuration_write
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.agent_device_script_binding import (
    AgentDeviceScriptRecord,
    AgentDeviceScriptState,
    AgentDeviceScriptWrite,
)
from app.services.ai_client import ai_client

router = APIRouter(dependencies=[Depends(require_configuration_write)])


@router.get(
    "/{agent_id}/device-script-binding", response_model=AgentDeviceScriptState
)
async def read(
    agent_id: UUID, actor: Staff = Depends(require_admin())
) -> AgentDeviceScriptState:
    return await ai_client.agent_device_script_binding(
        actor.project_id, actor.id, agent_id
    )


@router.put(
    "/{agent_id}/device-script-binding", response_model=AgentDeviceScriptRecord
)
async def save(
    agent_id: UUID,
    command: AgentDeviceScriptWrite,
    actor: Staff = Depends(require_admin()),
) -> AgentDeviceScriptRecord:
    return await ai_client.save_agent_device_script_binding(
        actor.project_id, actor.id, agent_id, command
    )


@router.get(
    "/{agent_id}/device-script-binding/requests/{request_id}",
    response_model=AgentDeviceScriptRecord,
)
async def recover(
    agent_id: UUID, request_id: UUID, actor: Staff = Depends(require_admin())
) -> AgentDeviceScriptRecord:
    return await ai_client.agent_device_script_binding_request(
        actor.project_id, actor.id, agent_id, request_id
    )
