"""API-only configuration; a binding cannot authorize a script execution."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agent_trials import (
    PrivateTrialRoute,
    TrialPrincipal,
    require_trial_principal,
)
from app.database import get_db
from app.schemas.agent_device_script_binding import (
    AgentDeviceScriptRecord,
    AgentDeviceScriptState,
    AgentDeviceScriptWrite,
)
from app.services.agent_device_script_bindings import (
    AgentDeviceScriptBindingService,
)

router = APIRouter(route_class=PrivateTrialRoute)


def get_service(
    db: AsyncSession = Depends(get_db),
) -> AgentDeviceScriptBindingService:
    return AgentDeviceScriptBindingService(db)


@router.get(
    "/{agent_id}/device-script-binding", response_model=AgentDeviceScriptState
)
async def read(
    agent_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptBindingService = Depends(get_service),
) -> AgentDeviceScriptState:
    return await service.read(
        principal.project_id, principal.staff_id, agent_id
    )


@router.put(
    "/{agent_id}/device-script-binding", response_model=AgentDeviceScriptRecord
)
async def save(
    agent_id: UUID,
    command: AgentDeviceScriptWrite,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptBindingService = Depends(get_service),
) -> AgentDeviceScriptRecord:
    return await service.save(
        principal.project_id, principal.staff_id, agent_id, command
    )


@router.get(
    "/{agent_id}/device-script-binding/requests/{request_id}",
    response_model=AgentDeviceScriptRecord,
)
async def recover(
    agent_id: UUID,
    request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptBindingService = Depends(get_service),
) -> AgentDeviceScriptRecord:
    return await service.request_result(
        principal.project_id, principal.staff_id, agent_id, request_id
    )
