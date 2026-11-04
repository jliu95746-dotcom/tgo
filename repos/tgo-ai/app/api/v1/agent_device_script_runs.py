"""Private approved business requests, isolated from the trial namespace."""

from collections.abc import Callable, Coroutine
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse, Response

from app.api.v1.agent_device_script_cases import get_service, no_store
from app.api.v1.agent_trials import (
    TrialPrincipal,
    require_trial_principal,
)
from app.schemas.agent_device_script_runs import (
    ScriptRunControl,
    ScriptRunStart,
    ScriptRunView,
)
from app.services.agent_device_script_cases import (
    AgentDeviceScriptCaseService,
)
from app.services.agent_device_script_runs import AgentDeviceScriptRuns


class PrivateScriptRunRoute(APIRoute):
    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()

        async def protected(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    headers={"Cache-Control": "no-store"},
                    content={
                        "detail": [
                            {
                                "loc": ["body"],
                                "type": "validation_error",
                                "msg": "业务执行字段无效",
                            }
                        ]
                    },
                )

        return protected


router = APIRouter(
    route_class=PrivateScriptRunRoute,
    dependencies=[Depends(no_store)],
)


@router.post(
    "/{agent_id}/device-script-runs", response_model=ScriptRunView
)
async def start(
    agent_id: UUID,
    command: ScriptRunStart,
    principal: TrialPrincipal = Depends(require_trial_principal),
    cases: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptRunView:
    return await AgentDeviceScriptRuns(cases).start(
        principal.project_id,
        principal.staff_id,
        agent_id,
        command,
    )


@router.get(
    "/{agent_id}/device-script-runs/requests/{request_id}",
    response_model=ScriptRunView,
)
async def read(
    agent_id: UUID,
    request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    cases: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptRunView:
    return await AgentDeviceScriptRuns(cases).get(
        principal.project_id,
        principal.staff_id,
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
    principal: TrialPrincipal = Depends(require_trial_principal),
    cases: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptRunView:
    if command.request_id != request_id:
        raise HTTPException(409, "业务执行原请求编号不一致")
    return await AgentDeviceScriptRuns(cases).cancel(
        principal.project_id,
        principal.staff_id,
        agent_id,
        request_id,
        command.report_id,
        command.review_id,
    )
