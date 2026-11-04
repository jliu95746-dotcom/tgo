"""API-only trials; comparison reports are not employee approval."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.api.v1.agent_trials import (
    PrivateTrialRoute,
    TrialPrincipal,
    require_trial_principal,
)
from app.schemas.agent_device_script_trials import (
    ScriptTrialStart,
    ScriptTrialReport,
)
from app.services.agent_device_script_trials import (
    AgentDeviceScriptTrialService,
)
from app.schemas.agent_device_script_requests import (
    ScriptTrialControl, ScriptTrialRequestState,
)
from app.services.agent_device_script_requests import ScriptTrialRequests

router = APIRouter(route_class=PrivateTrialRoute)


def get_service(
    db: AsyncSession = Depends(get_db),
) -> AgentDeviceScriptTrialService:
    return AgentDeviceScriptTrialService(db)


@router.get(
    "/{agent_id}/device-script-trials/requests/{request_id}/state",
    response_model=ScriptTrialRequestState,
)
async def request_state(
    agent_id: UUID, request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptTrialService = Depends(get_service),
) -> ScriptTrialRequestState:
    return await ScriptTrialRequests(service).state(
        principal.project_id, principal.staff_id, agent_id, request_id
    )


@router.post(
    "/{agent_id}/device-script-trials/requests/{request_id}/cancel",
    response_model=ScriptTrialRequestState,
)
async def cancel_request(
    agent_id: UUID, request_id: UUID, command: ScriptTrialControl,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptTrialService = Depends(get_service),
) -> ScriptTrialRequestState:
    if request_id != command.request_id:
        raise HTTPException(422, "请求路径与原编号不一致")
    return await ScriptTrialRequests(service).cancel(
        principal.project_id, principal.staff_id, agent_id, command
    )


@router.post(
    "/{agent_id}/device-script-trials", response_model=ScriptTrialReport
)
async def start(
    agent_id: UUID,
    command: ScriptTrialStart,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptTrialService = Depends(get_service),
) -> ScriptTrialReport:
    return await service.start(
        principal.project_id, principal.staff_id, agent_id, command
    )


@router.get(
    "/{agent_id}/device-script-trials/requests/{request_id}",
    response_model=ScriptTrialReport,
)
async def recover(
    agent_id: UUID,
    request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptTrialService = Depends(get_service),
) -> ScriptTrialReport:
    return await service.by_request(
        principal.project_id, principal.staff_id, agent_id, request_id
    )


@router.get(
    "/{agent_id}/device-script-trials/{report_id}",
    response_model=ScriptTrialReport,
)
async def read(
    agent_id: UUID,
    report_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptTrialService = Depends(get_service),
) -> ScriptTrialReport:
    return await service.get(
        principal.project_id, principal.staff_id, agent_id, report_id
    )
