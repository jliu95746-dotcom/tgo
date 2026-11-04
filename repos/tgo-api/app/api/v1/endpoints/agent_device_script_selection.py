"""Current administrators pin one original approval for an employee."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.company_configuration_access import require_configuration_write
from app.api.v1.endpoints.agent_device_script_cases import no_store
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.agent_device_script_selection import (
    ScriptSelectionRecord,
    ScriptSelectionState,
    ScriptSelectionWrite,
)
from app.services.ai_client import ai_client
from app.services.request_execution import run_until_disconnect

router = APIRouter(
    dependencies=[
        Depends(require_configuration_write),
        Depends(no_store),
    ]
)


@router.get(
    "/{agent_id}/device-script-selection",
    response_model=ScriptSelectionState,
)
async def read(
    agent_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> ScriptSelectionState:
    return await ai_client.agent_device_script_selection(
        actor.project_id,
        actor.id,
        agent_id,
    )


@router.post(
    "/{agent_id}/device-script-selection",
    response_model=ScriptSelectionRecord,
)
async def save(
    agent_id: UUID,
    command: ScriptSelectionWrite,
    request: Request,
    actor: Staff = Depends(require_admin()),
) -> ScriptSelectionRecord:
    return await run_until_disconnect(
        lambda: ai_client.save_agent_device_script_selection(
            actor.project_id,
            actor.id,
            agent_id,
            command,
        ),
        request,
    )


@router.get(
    "/{agent_id}/device-script-selection/requests/{request_id}",
    response_model=ScriptSelectionRecord,
)
async def original(
    agent_id: UUID,
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> ScriptSelectionRecord:
    return await ai_client.agent_device_script_selection_request(
        actor.project_id,
        actor.id,
        agent_id,
        request_id,
    )
