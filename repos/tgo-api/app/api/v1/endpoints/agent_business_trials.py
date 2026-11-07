"Current administrator proxies reports; customer assertions are verified \
privately."

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.business_trial_route import BusinessTrialRoute
from app.api.company_configuration_access import require_configuration_write
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.agent_business_trials import (
    BusinessTrialRequest,
    BusinessTrialReport,
)
from app.schemas.agent_business_requests import (
    BusinessRequestControl,
    BusinessRequestState,
    BusinessEmployeeCurrentState,
)
from app.services.ai_client import ai_client
from app.services import agent_business_trial_client as trials
from app.schemas.agent_business_reviews import (
    BusinessReviewRequest,
    BusinessReviewRecord,
    BusinessReviewState,
    AgentBusinessCapabilities,
)
from app.schemas.agent_business_review_requests import (
    BusinessReviewRequestState,
)

router = APIRouter(
    route_class=BusinessTrialRoute,
    dependencies=[Depends(require_configuration_write)],
)


@router.get(
    "/{agent_id}/business-current-state",
    response_model=BusinessEmployeeCurrentState,
)
async def current_employee(
    agent_id: UUID, actor: Staff = Depends(require_admin())
) -> BusinessEmployeeCurrentState:
    return await trials.current_employee(
        ai_client, actor.project_id, actor.id, agent_id
    )


@router.get(
    "/{agent_id}/business-trials/requests/{request_id}/state",
    response_model=BusinessRequestState,
)
async def request_state(
    agent_id: UUID, request_id: UUID, actor: Staff = Depends(require_admin())
) -> BusinessRequestState:
    return await trials.request_state(
        ai_client, actor.project_id, actor.id, agent_id, request_id
    )


@router.post(
    "/{agent_id}/business-trials/requests/{request_id}/cancel",
    response_model=BusinessRequestState,
)
async def cancel_request(
    agent_id: UUID,
    request_id: UUID,
    command: BusinessRequestControl,
    actor: Staff = Depends(require_admin()),
) -> BusinessRequestState:
    return await trials.request_state(
        ai_client,
        actor.project_id,
        actor.id,
        agent_id,
        request_id,
        cancel=command,
    )


@router.get(
    (
        '/{agent_id}/business-trials/{report_id}/reviews/requests/{request_id'
        '}/state'
    ),
    response_model=BusinessReviewRequestState,
)
async def review_request_state(
    agent_id: UUID,
    report_id: UUID,
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> BusinessReviewRequestState:
    return await trials.review_request_state(
        ai_client, actor.project_id, actor.id, agent_id, report_id, request_id
    )


@router.post(
    (
        '/{agent_id}/business-trials/{report_id}/reviews/requests/{request_id'
        '}/resume'
    ),
    response_model=BusinessReviewRecord,
)
async def resume_review_request(
    agent_id: UUID,
    report_id: UUID,
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> BusinessReviewRecord:
    return await trials.resume_review(
        ai_client, actor.project_id, actor.id, agent_id, report_id, request_id
    )


@router.post(
    (
        '/{agent_id}/business-trials/{report_id}/reviews/requests/{request_id'
        '}/cancel'
    ),
    response_model=BusinessReviewRequestState,
)
async def cancel_review_request(
    agent_id: UUID,
    report_id: UUID,
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> BusinessReviewRequestState:
    return await trials.review_request_state(
        ai_client,
        actor.project_id,
        actor.id,
        agent_id,
        report_id,
        request_id,
        cancel=True,
    )


@router.get(
    "/{agent_id}/business-capabilities",
    response_model=AgentBusinessCapabilities,
)
async def capabilities(
    agent_id: UUID, actor: Staff = Depends(require_admin())
) -> AgentBusinessCapabilities:
    return await trials.capabilities(
        ai_client, actor.project_id, actor.id, agent_id
    )


@router.get(
    "/{agent_id}/business-trials/{report_id}/reviews",
    response_model=BusinessReviewState,
)
async def review_state(
    agent_id: UUID, report_id: UUID, actor: Staff = Depends(require_admin())
) -> BusinessReviewState:
    return await trials.review_state(
        ai_client, actor.project_id, actor.id, agent_id, report_id
    )


@router.post(
    "/{agent_id}/business-trials/{report_id}/reviews",
    response_model=BusinessReviewRecord,
)
async def review(
    agent_id: UUID,
    report_id: UUID,
    command: BusinessReviewRequest,
    actor: Staff = Depends(require_admin()),
) -> BusinessReviewRecord:
    return await trials.review(
        ai_client,
        actor.project_id,
        actor.id,
        agent_id,
        report_id,
        command=command,
    )


@router.get(
    "/{agent_id}/business-trials/{report_id}/reviews/requests/{request_id}",
    response_model=BusinessReviewRecord,
)
async def recover_review(
    agent_id: UUID,
    report_id: UUID,
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
) -> BusinessReviewRecord:
    return await trials.review(
        ai_client,
        actor.project_id,
        actor.id,
        agent_id,
        report_id,
        request_id=request_id,
    )


@router.post(
    "/{agent_id}/business-trials",
    response_model=BusinessTrialReport,
    status_code=202,
)
async def start(
    agent_id: UUID,
    payload: BusinessTrialRequest,
    actor: Staff = Depends(require_admin()),
) -> BusinessTrialReport:
    return await trials.report(
        ai_client, actor.project_id, actor.id, agent_id, command=payload
    )


@router.get(
    "/{agent_id}/business-trials/requests/{request_id}",
    response_model=BusinessTrialReport,
)
async def recover(
    agent_id: UUID, request_id: UUID, actor: Staff = Depends(require_admin())
) -> BusinessTrialReport:
    return await trials.report(
        ai_client, actor.project_id, actor.id, agent_id, request_id=request_id
    )


@router.get(
    "/{agent_id}/business-trials/{report_id}",
    response_model=BusinessTrialReport,
)
async def read(
    agent_id: UUID, report_id: UUID, actor: Staff = Depends(require_admin())
) -> BusinessTrialReport:
    return await trials.report(
        ai_client, actor.project_id, actor.id, agent_id, report_id=report_id
    )


@router.post(
    "/{agent_id}/business-trials/{report_id}/cancel",
    response_model=BusinessTrialReport,
)
async def cancel(
    agent_id: UUID, report_id: UUID, actor: Staff = Depends(require_admin())
) -> BusinessTrialReport:
    return await trials.report(
        ai_client,
        actor.project_id,
        actor.id,
        agent_id,
        report_id=report_id,
        cancel=True,
    )
