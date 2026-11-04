"""Current administrators manage versioned cases and explicitly read bodies."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.company_configuration_access import require_configuration_write
from app.core.security import require_admin
from app.models.staff import Staff
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
from app.services.ai_client import ai_client
from app.services.request_execution import run_until_disconnect


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


router = APIRouter(
    dependencies=[Depends(require_configuration_write), Depends(no_store)]
)


@router.get("/{agent_id}/device-script-cases", response_model=ScriptCasePage)
async def list_cases(
    agent_id: UUID,
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(20, ge=1, le=100),
    actor: Staff = Depends(require_admin()),
) -> ScriptCasePage:
    return await ai_client.list_agent_device_script_cases(
        actor.project_id, actor.id, agent_id, skip=skip, limit=limit
    )


@router.post(
    "/{agent_id}/device-script-cases", response_model=ScriptCaseRecord
)
async def save_case(
    agent_id: UUID,
    command: ScriptCaseWrite,
    actor: Staff = Depends(require_admin()),
) -> ScriptCaseRecord:
    return await ai_client.save_agent_device_script_case(
        actor.project_id, actor.id, agent_id, command
    )


@router.get(
    "/{agent_id}/device-script-cases/requests/{request_id}",
    response_model=ScriptCaseRecord,
)
async def recover_case(
    agent_id: UUID,
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> ScriptCaseRecord:
    return await ai_client.agent_device_script_case_request(
        actor.project_id, actor.id, agent_id, request_id
    )


@router.get(
    "/{agent_id}/device-script-cases/versions/{version_id}",
    response_model=ScriptCaseRead,
)
async def read_case(
    agent_id: UUID,
    version_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> ScriptCaseRead:
    return await ai_client.get_agent_device_script_case(
        actor.project_id, actor.id, agent_id, version_id
    )


@router.post(
    "/{agent_id}/device-script-cases/versions/{version_id}/trials",
    response_model=ScriptCaseTrialResult,
)
async def run_case(
    agent_id: UUID,
    version_id: UUID,
    command: ScriptTrialControl,
    request: Request,
    actor: Staff = Depends(require_admin()),
) -> ScriptCaseTrialResult:
    return await run_until_disconnect(
        lambda: ai_client.run_agent_device_script_case(
            actor.project_id, actor.id, agent_id, version_id, command
        ),
        request,
    )


@router.get(
    "/{agent_id}/device-script-trials/{report_id}/evidence",
    response_model=ScriptTrialEvidence,
)
async def read_evidence(
    agent_id: UUID,
    report_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> ScriptTrialEvidence:
    return await ai_client.agent_device_script_evidence(
        actor.project_id, actor.id, agent_id, report_id
    )


@router.get(
    "/{agent_id}/device-script-cases/requests/{request_id}/state",
    response_model=ScriptCaseSaveState,
)
async def save_state(
    agent_id: UUID,
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> ScriptCaseSaveState:
    return await ai_client.agent_device_script_case_save_state(
        actor.project_id, actor.id, agent_id, request_id
    )


@router.post(
    "/{agent_id}/device-script-cases/requests/{request_id}/cancel",
    response_model=ScriptCaseSaveState,
)
async def cancel_save(
    agent_id: UUID,
    request_id: UUID,
    command: ScriptCaseSaveControl,
    actor: Staff = Depends(require_admin()),
) -> ScriptCaseSaveState:
    from fastapi import HTTPException

    if command.request_id != request_id:
        raise HTTPException(409, "用例保存原请求编号不一致")
    return await ai_client.cancel_agent_device_script_case_save(
        actor.project_id, actor.id, agent_id, command
    )


@router.get(
    "/{agent_id}/device-script-trials/{report_id}/review",
    response_model=ScriptReviewState,
)
async def read_review(
    agent_id: UUID,
    report_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> ScriptReviewState:
    return await ai_client.agent_device_script_review(
        actor.project_id, actor.id, agent_id, report_id
    )


@router.get(
    "/{agent_id}/device-script-trials/{report_id}/review/run-check",
    response_model=ScriptRunCheck,
)
async def check_script_run(
    agent_id: UUID,
    report_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> ScriptRunCheck:
    return await ai_client.agent_device_script_run_check(
        actor.project_id, actor.id, agent_id, report_id
    )


@router.post(
    "/{agent_id}/device-script-trials/{report_id}/review",
    response_model=ScriptReviewState,
)
async def decide_review(
    agent_id: UUID,
    report_id: UUID,
    command: ScriptReviewWrite,
    actor: Staff = Depends(require_admin()),
) -> ScriptReviewState:
    return await ai_client.agent_device_script_review(
        actor.project_id, actor.id, agent_id, report_id, command=command
    )


@router.post(
    "/{agent_id}/device-script-trials/{report_id}/review/{review_id}/revoke",
    response_model=ScriptReviewState,
)
async def revoke_review(
    agent_id: UUID,
    report_id: UUID,
    review_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> ScriptReviewState:
    return await ai_client.agent_device_script_review(
        actor.project_id, actor.id, agent_id, report_id, revoke=review_id
    )
