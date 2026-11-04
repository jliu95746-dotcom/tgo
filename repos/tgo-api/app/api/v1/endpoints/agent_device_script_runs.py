"""Current administrators execute and recover the original approved request."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.company_configuration_access import (
    require_configuration_write,
)
from app.api.v1.endpoints.agent_device_script_cases import no_store
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.agent_device_script_runs import (
    ScriptRunControl,
    ScriptRunStart,
    ScriptRunView,
)
from app.services.ai_client import ai_client
from app.services.request_execution import run_until_disconnect

router = APIRouter(
    dependencies=[
        Depends(require_configuration_write),
        Depends(no_store),
    ],
)


@router.post(
    "/{agent_id}/device-script-runs", response_model=ScriptRunView
)
async def start(
    agent_id: UUID,
    command: ScriptRunStart,
    request: Request,
    actor: Staff = Depends(require_admin()),
) -> ScriptRunView:
    return await run_until_disconnect(
        lambda: ai_client.start_agent_device_script_run(
            actor.project_id,
            actor.id,
            agent_id,
            command,
        ),
        request,
    )


@router.get(
    "/{agent_id}/device-script-runs/requests/{request_id}",
    response_model=ScriptRunView,
)
async def read(
    agent_id: UUID,
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> ScriptRunView:
    return await ai_client.agent_device_script_run(
        actor.project_id,
        actor.id,
        agent_id,
        request_id,
    )


@router.post(
    "/{agent_id}/device-script-runs/requests/{request_id}/cancel",
    response_model=ScriptRunView,
)
async def cancel(
    agent_id: UUID,
    request_id: UUID,
    command: ScriptRunControl,
    actor: Staff = Depends(require_admin()),
) -> ScriptRunView:
    if command.request_id != request_id:
        raise HTTPException(409, "业务执行原请求编号不一致")
    return await ai_client.cancel_agent_device_script_run(
        actor.project_id,
        actor.id,
        agent_id,
        command,
    )
