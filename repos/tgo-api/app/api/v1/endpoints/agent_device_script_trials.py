"""Admin-only script trials using identity from the current login."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.company_configuration_access import require_configuration_write
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.agent_device_script_trials import (
    ScriptTrialReport,
    ScriptTrialStart,
)
from app.services.ai_client import ai_client
from app.services.request_execution import run_until_disconnect
from app.schemas.agent_device_script_requests import (
    ScriptTrialControl, ScriptTrialRequestState,
)

router = APIRouter(dependencies=[Depends(require_configuration_write)])


@router.get(
    "/{agent_id}/device-script-trials/requests/{request_id}/state",
    response_model=ScriptTrialRequestState,
)
async def request_state(
    agent_id: UUID, request_id: UUID, actor: Staff = Depends(require_admin()),
) -> ScriptTrialRequestState:
    return await ai_client.agent_device_script_request_state(
        actor.project_id, actor.id, agent_id, request_id
    )


@router.post(
    "/{agent_id}/device-script-trials/requests/{request_id}/cancel",
    response_model=ScriptTrialRequestState,
)
async def cancel_request(
    agent_id: UUID, request_id: UUID, command: ScriptTrialControl,
    actor: Staff = Depends(require_admin()),
) -> ScriptTrialRequestState:
    if request_id != command.request_id:
        raise HTTPException(422, "请求路径与原编号不一致")
    return await ai_client.cancel_agent_device_script_request(
        actor.project_id, actor.id, agent_id, command
    )


@router.post(
    "/{agent_id}/device-script-trials", response_model=ScriptTrialReport
)
async def start(
    agent_id: UUID, command: ScriptTrialStart, request: Request,
    actor: Staff = Depends(require_admin()),
) -> ScriptTrialReport:
    return await run_until_disconnect(
        lambda: ai_client.start_agent_device_script_trial(
            actor.project_id, actor.id, agent_id, command
        ), request,
    )


@router.get(
    "/{agent_id}/device-script-trials/requests/{request_id}",
    response_model=ScriptTrialReport,
)
async def recover(
    agent_id: UUID, request_id: UUID, actor: Staff = Depends(require_admin()),
) -> ScriptTrialReport:
    return await ai_client.agent_device_script_trial_request(
        actor.project_id, actor.id, agent_id, request_id
    )


@router.get(
    "/{agent_id}/device-script-trials/{report_id}",
    response_model=ScriptTrialReport,
)
async def read(
    agent_id: UUID, report_id: UUID, actor: Staff = Depends(require_admin()),
) -> ScriptTrialReport:
    return await ai_client.get_agent_device_script_trial(
        actor.project_id, actor.id, agent_id, report_id
    )
