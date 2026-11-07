"Persist before dispatch; restart recovery reads reports without \
replaying queries."

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agent_trials import (
    PrivateTrialRoute,
    TrialPrincipal,
    require_trial_principal,
)
from app.database import AsyncSessionLocal, get_db
from app.schemas.agent_business_trials import (
    BusinessTrialRequest,
    BusinessTrialReport,
    BusinessTrialCommand,
    BusinessTrialCheck,
    BusinessTrialAuthority,
)
from app.services.agent_business_trials import AgentBusinessTrialService
from app.services.agent_business_preparation import AgentBusinessPreparation
from app.schemas.agent_business_requests import (
    BusinessRequestControl,
    BusinessRequestState,
    BusinessEmployeeCurrentState,
)
from app.schemas.agent_business_reviews import (
    BusinessReviewRequest,
    BusinessReviewRecord,
    BusinessReviewState,
    AgentBusinessCapabilities,
)
from app.services.agent_business_reviews import AgentBusinessReviewService
from app.services.agent_business_review_requests import (
    AgentBusinessReviewRequests,
)
from app.schemas.agent_business_review_requests import (
    BusinessReviewRequestState,
)
from app.services.agent_business_capabilities import (
    AgentBusinessCapabilityService,
)

router = APIRouter(route_class=PrivateTrialRoute)


def get_service(
    db: AsyncSession = Depends(get_db),
) -> AgentBusinessTrialService:
    return AgentBusinessTrialService(db)


def get_reviews(
    trials: AgentBusinessTrialService = Depends(get_service),
) -> AgentBusinessReviewService:
    return AgentBusinessReviewService(trials.db, trials)


@router.get(
    "/{agent_id}/business-current-state",
    response_model=BusinessEmployeeCurrentState,
)
async def current_employee(
    agent_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessTrialService = Depends(get_service),
) -> BusinessEmployeeCurrentState:
    return await service.current_employee(
        principal.project_id, principal.staff_id, agent_id
    )


@router.get(
    "/{agent_id}/business-capabilities",
    response_model=AgentBusinessCapabilities,
)
async def capabilities(
    agent_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    reviews: AgentBusinessReviewService = Depends(get_reviews),
) -> AgentBusinessCapabilities:
    return await AgentBusinessCapabilityService(reviews.db, reviews).read(
        principal.project_id, principal.staff_id, agent_id
    )


@router.get(
    "/{agent_id}/business-trials/{report_id}/reviews",
    response_model=BusinessReviewState,
)
async def review_state(
    agent_id: UUID,
    report_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    reviews: AgentBusinessReviewService = Depends(get_reviews),
) -> BusinessReviewState:
    return await reviews.state(
        principal.project_id, principal.staff_id, agent_id, report_id
    )


@router.post(
    "/{agent_id}/business-trials/{report_id}/reviews",
    response_model=BusinessReviewRecord,
)
async def review(
    agent_id: UUID,
    report_id: UUID,
    command: BusinessReviewRequest,
    principal: TrialPrincipal = Depends(require_trial_principal),
    reviews: AgentBusinessReviewService = Depends(get_reviews),
) -> BusinessReviewRecord:
    return await reviews.review(
        principal.project_id, principal.staff_id, agent_id, report_id, command
    )


@router.get(
    "/{agent_id}/business-trials/{report_id}/reviews/requests/{request_id}",
    response_model=BusinessReviewRecord,
)
async def recover_review(
    agent_id: UUID,
    report_id: UUID,
    request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    reviews: AgentBusinessReviewService = Depends(get_reviews),
) -> BusinessReviewRecord:
    return await reviews.request_result(
        principal.project_id,
        principal.staff_id,
        agent_id,
        report_id,
        request_id,
    )


async def execute(command: BusinessTrialCommand) -> None:
    async with AsyncSessionLocal() as db:
        await AgentBusinessTrialService(db).run(command)


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
    principal: TrialPrincipal = Depends(require_trial_principal),
    reviews: AgentBusinessReviewService = Depends(get_reviews),
) -> BusinessReviewRequestState:
    return await AgentBusinessReviewRequests(reviews).state(
        principal.project_id,
        principal.staff_id,
        agent_id,
        report_id,
        request_id,
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
    principal: TrialPrincipal = Depends(require_trial_principal),
    reviews: AgentBusinessReviewService = Depends(get_reviews),
) -> BusinessReviewRecord:
    return await AgentBusinessReviewRequests(reviews).resume(
        principal.project_id,
        principal.staff_id,
        agent_id,
        report_id,
        request_id,
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
    principal: TrialPrincipal = Depends(require_trial_principal),
    reviews: AgentBusinessReviewService = Depends(get_reviews),
) -> BusinessReviewRequestState:
    return await AgentBusinessReviewRequests(reviews).cancel(
        principal.project_id,
        principal.staff_id,
        agent_id,
        report_id,
        request_id,
    )


@router.get(
    "/{agent_id}/business-trials/requests/{request_id}/state",
    response_model=BusinessRequestState,
)
async def request_state(
    agent_id: UUID,
    request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessTrialService = Depends(get_service),
) -> BusinessRequestState:
    return await AgentBusinessPreparation(service).state(
        principal.project_id, principal.staff_id, agent_id, request_id
    )


@router.post(
    "/{agent_id}/business-trials/requests/{request_id}/cancel",
    response_model=BusinessRequestState,
)
async def cancel_request(
    agent_id: UUID,
    request_id: UUID,
    command: BusinessRequestControl,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessTrialService = Depends(get_service),
) -> BusinessRequestState:
    if command.request_id != request_id:
        raise HTTPException(409, "取消命令与原请求编号不一致")
    return await AgentBusinessPreparation(service).cancel(
        principal.project_id, principal.staff_id, agent_id, command
    )


@router.post(
    "/{agent_id}/business-trials",
    response_model=BusinessTrialReport,
    status_code=202,
)
async def start(
    agent_id: UUID,
    payload: BusinessTrialRequest,
    tasks: BackgroundTasks,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessTrialService = Depends(get_service),
) -> BusinessTrialReport:
    report, command = await service.prepare(
        principal.project_id, principal.staff_id, agent_id, payload
    )
    if command is not None:
        tasks.add_task(execute, command)
    return report


@router.post(
    "/{agent_id}/business-trials/authorize",
    response_model=BusinessTrialAuthority,
)
async def authorize(
    agent_id: UUID,
    check: BusinessTrialCheck,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessTrialService = Depends(get_service),
) -> BusinessTrialAuthority:
    if (
        check.command.agent_id != agent_id
        or check.command.project_id != principal.project_id
        or check.command.actor_id != principal.staff_id
    ):
        raise HTTPException(403, "业务测试内部身份不一致")
    return await service.authorize(check)


@router.get(
    "/{agent_id}/business-trials/requests/{request_id}",
    response_model=BusinessTrialReport,
)
async def recover(
    agent_id: UUID,
    request_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessTrialService = Depends(get_service),
) -> BusinessTrialReport:
    return await service.by_request(
        principal.project_id, principal.staff_id, agent_id, request_id
    )


@router.get(
    "/{agent_id}/business-trials/{report_id}",
    response_model=BusinessTrialReport,
)
async def read(
    agent_id: UUID,
    report_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessTrialService = Depends(get_service),
) -> BusinessTrialReport:
    return await service.get(
        principal.project_id, principal.staff_id, agent_id, report_id
    )


@router.post(
    "/{agent_id}/business-trials/{report_id}/cancel",
    response_model=BusinessTrialReport,
)
async def cancel(
    agent_id: UUID,
    report_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentBusinessTrialService = Depends(get_service),
) -> BusinessTrialReport:
    return await service.cancel(
        principal.project_id, principal.staff_id, agent_id, report_id
    )
