"""Trusted administrator case management; retained bodies are never cached."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agent_trials import (
    PrivateTrialRoute,
    TrialPrincipal,
    require_trial_principal,
)
from app.database import get_db
from app.schemas.agent_device_script_cases import (
    ScriptCasePage,
    ScriptCaseSaveControl,
    ScriptCaseSaveState,
    ScriptCaseRead,
    ScriptCaseRecord,
    ScriptCaseWrite,
    ScriptCaseTrialResult,
    ScriptTrialEvidence,
)
from app.schemas.agent_device_script_requests import ScriptTrialControl
from app.schemas.agent_device_script_reviews import (
    ScriptReviewState, ScriptReviewWrite, ScriptRunCheck,
)
from app.services.agent_device_script_reviews import AgentDeviceScriptReviews
from app.services.agent_device_script_cases import AgentDeviceScriptCaseService
from app.services.agent_device_script_trials import (
    AgentDeviceScriptTrialService,
)


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


router = APIRouter(
    route_class=PrivateTrialRoute, dependencies=[Depends(no_store)]
)


def get_service(
    db: AsyncSession = Depends(get_db),
) -> AgentDeviceScriptCaseService:
    return AgentDeviceScriptCaseService(AgentDeviceScriptTrialService(db))


@router.get("/{agent_id}/device-script-cases", response_model=ScriptCasePage)
async def list_cases(
    agent_id: UUID,
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(20, ge=1, le=100),
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptCasePage:
    return await service.list(
        principal.project_id,
        principal.staff_id,
        agent_id,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/{agent_id}/device-script-cases", response_model=ScriptCaseRecord
)
async def save_case(
    agent_id: UUID,
    command: ScriptCaseWrite,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptCaseRecord:
    return await service.save(
        principal.project_id, principal.staff_id, agent_id, command
    )


@router.get(
    "/{agent_id}/device-script-cases/requests/{request_id}",
    response_model=ScriptCaseRecord,
)
async def recover_case(
    agent_id: UUID,
    request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptCaseRecord:
    return await service.by_request(
        principal.project_id, principal.staff_id, agent_id, request_id
    )


@router.get(
    "/{agent_id}/device-script-cases/versions/{version_id}",
    response_model=ScriptCaseRead,
)
async def read_case(
    agent_id: UUID,
    version_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptCaseRead:
    return await service.get(
        principal.project_id, principal.staff_id, agent_id, version_id
    )


@router.post(
    "/{agent_id}/device-script-cases/versions/{version_id}/trials",
    response_model=ScriptCaseTrialResult,
)
async def run_case(
    agent_id: UUID,
    version_id: UUID,
    command: ScriptTrialControl,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptCaseTrialResult:
    return ScriptCaseTrialResult(
        case_version_id=version_id,
        report=await service.run(
            principal.project_id,
            principal.staff_id,
            agent_id,
            version_id,
            command,
        ),
    )


@router.get(
    "/{agent_id}/device-script-trials/{report_id}/evidence",
    response_model=ScriptTrialEvidence,
)
async def read_evidence(
    agent_id: UUID,
    report_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptTrialEvidence:
    return await service.evidence(
        principal.project_id, principal.staff_id, agent_id, report_id
    )


@router.get(
    "/{agent_id}/device-script-cases/requests/{request_id}/state",
    response_model=ScriptCaseSaveState,
)
async def save_state(
    agent_id: UUID,
    request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptCaseSaveState:
    return await service.save_state(
        principal.project_id, principal.staff_id, agent_id, request_id
    )


@router.post(
    "/{agent_id}/device-script-cases/requests/{request_id}/cancel",
    response_model=ScriptCaseSaveState,
)
async def cancel_save(
    agent_id: UUID,
    request_id: UUID,
    command: ScriptCaseSaveControl,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptCaseSaveState:
    from app.exceptions import ConflictError

    if command.request_id != request_id:
        raise ConflictError("用例保存原请求编号不一致")
    return await service.cancel_save(
        principal.project_id, principal.staff_id, agent_id, command
    )


@router.get(
    "/{agent_id}/device-script-trials/{report_id}/review",
    response_model=ScriptReviewState,
)
async def read_review(
    agent_id: UUID,
    report_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptReviewState:
    return await AgentDeviceScriptReviews(service).read(
        principal.project_id, principal.staff_id, agent_id, report_id
    )


@router.get(
    "/{agent_id}/device-script-trials/{report_id}/review/run-check",
    response_model=ScriptRunCheck,
)
async def check_script_run(
    agent_id: UUID,
    report_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptRunCheck:
    return await AgentDeviceScriptReviews(service).check_run(
        principal.project_id, principal.staff_id, agent_id, report_id
    )


@router.post(
    "/{agent_id}/device-script-trials/{report_id}/review",
    response_model=ScriptReviewState,
)
async def decide_review(
    agent_id: UUID,
    report_id: UUID,
    command: ScriptReviewWrite,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptReviewState:
    return await AgentDeviceScriptReviews(service).decide(
        principal.project_id, principal.staff_id, agent_id, report_id, command
    )


@router.post(
    "/{agent_id}/device-script-trials/{report_id}/review/{review_id}/revoke",
    response_model=ScriptReviewState,
)
async def revoke_review(
    agent_id: UUID,
    report_id: UUID,
    review_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDeviceScriptCaseService = Depends(get_service),
) -> ScriptReviewState:
    return await AgentDeviceScriptReviews(service).revoke(
        principal.project_id, principal.staff_id, agent_id, report_id,
        review_id,
    )
