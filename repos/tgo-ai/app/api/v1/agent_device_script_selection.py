"""Trusted administrator selection of a specific original approval."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.v1.agent_device_script_cases import get_service, no_store
from app.api.v1.agent_device_script_runs import PrivateScriptRunRoute
from app.api.v1.agent_trials import TrialPrincipal, require_trial_principal
from app.schemas.agent_device_script_selection import (
    ScriptSelectionRecord,
    ScriptSelectionState,
    ScriptSelectionWrite,
)
from app.services.agent_device_script_cases import AgentDeviceScriptCaseService
from app.services.agent_device_script_selection import ScriptSelectionService

router = APIRouter(
    route_class=PrivateScriptRunRoute,
    dependencies=[Depends(no_store)],
)


@router.get(
    "/{agent_id}/device-script-selection",
    response_model=ScriptSelectionState,
)
async def read(
    agent_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    cases: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptSelectionState:
    return await ScriptSelectionService(cases).read(
        principal.project_id,
        principal.staff_id,
        agent_id,
    )


@router.post(
    "/{agent_id}/device-script-selection",
    response_model=ScriptSelectionRecord,
)
async def save(
    agent_id: UUID,
    command: ScriptSelectionWrite,
    principal: TrialPrincipal = Depends(require_trial_principal),
    cases: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptSelectionRecord:
    return await ScriptSelectionService(cases).save(
        principal.project_id,
        principal.staff_id,
        agent_id,
        command,
    )


@router.get(
    "/{agent_id}/device-script-selection/requests/{request_id}",
    response_model=ScriptSelectionRecord,
)
async def request_result(
    agent_id: UUID,
    request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    cases: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptSelectionRecord:
    return await ScriptSelectionService(cases).request_result(
        principal.project_id,
        principal.staff_id,
        agent_id,
        request_id,
    )
