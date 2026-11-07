"""Trusted API-only trials of inactive employees; never public auth fallback."""

import hmac
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.database import get_db
from app.schemas.agent_knowledge_capabilities import (
    AgentKnowledgeCapabilities,
    KnowledgeCapabilityQuery,
)
from app.services.agent_knowledge_capabilities import (
    AgentKnowledgeCapabilityService,
)
from app.dependencies import get_supervisor_runtime_service
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.schemas.agent_trials import AgentTrialReport, AgentTrialRequest
from app.services.agent_trials import AgentTrialService
from app.schemas.agent_trial_reviews import (
    KnowledgeReviewCommand,
    KnowledgeReviewQuery,
    KnowledgeReviewResponse,
    KnowledgeReviewState,
)
from app.services.agent_trial_reviews import AgentTrialReviewService
from app.services.quota_authorization import authorize
from app.streaming.owned_response import run_until_disconnect


class PrivateTrialRoute(APIRoute):
    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()

        async def protected(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                # FastAPI's default error includes input, which may contain a
                # rejected credential object or a private customer question.
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {
                                "loc": item["loc"],
                                "type": item["type"],
                                "msg": "试聊字段无效",
                            }
                            for item in exc.errors()
                        ]
                    },
                )

        return protected


@dataclass(frozen=True)
class TrialPrincipal:
    project_id: UUID
    staff_id: UUID


async def require_trial_principal(request: Request) -> TrialPrincipal:
    key = request.headers.get("X-Internal-API-Key", "")
    if not key or not hmac.compare_digest(key, settings.secret_key):
        raise HTTPException(403, "试聊仅允许通过管理端授权接口访问")
    try:
        return TrialPrincipal(
            project_id=UUID(request.headers.get("X-Project-Id", "")),
            staff_id=UUID(request.headers.get("X-Staff-Id", "")),
        )
    except ValueError:
        raise HTTPException(403, "试聊内部身份无效") from None


def get_trial_service(
    db: AsyncSession = Depends(get_db),
    runtime: SupervisorRuntimeService = Depends(
        get_supervisor_runtime_service
    ),
) -> AgentTrialService:
    return AgentTrialService(db, runtime)


router = APIRouter(route_class=PrivateTrialRoute)


@router.post(
    "/{agent_id}/knowledge-capabilities",
    response_model=AgentKnowledgeCapabilities,
)
async def knowledge_capabilities(
    agent_id: UUID,
    payload: KnowledgeCapabilityQuery,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentTrialService = Depends(get_trial_service),
) -> AgentKnowledgeCapabilities:
    return await AgentKnowledgeCapabilityService(
        AgentTrialReviewService(service.db, service)
    ).read(
        principal.project_id,
        principal.staff_id,
        agent_id,
        payload,
    )


@router.post(
    "/{agent_id}/trials/{report_id}/reviews",
    response_model=KnowledgeReviewResponse,
)
async def review_trial(
    agent_id: UUID,
    report_id: UUID,
    payload: KnowledgeReviewCommand,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentTrialService = Depends(get_trial_service),
) -> KnowledgeReviewResponse:
    return await AgentTrialReviewService(service.db, service).review(
        principal.project_id,
        principal.staff_id,
        agent_id,
        report_id,
        payload,
    )


@router.post(
    "/{agent_id}/trials/{report_id}/review-state",
    response_model=KnowledgeReviewState,
)
async def trial_reviews(
    agent_id: UUID,
    report_id: UUID,
    payload: KnowledgeReviewQuery,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentTrialService = Depends(get_trial_service),
) -> KnowledgeReviewState:
    return await AgentTrialReviewService(service.db, service).state(
        principal.project_id,
        principal.staff_id,
        agent_id,
        report_id,
        payload.cases,
    )


@router.post("/{agent_id}/trials", response_model=AgentTrialReport)
async def start_trial(
    agent_id: UUID,
    payload: AgentTrialRequest,
    request: Request,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentTrialService = Depends(get_trial_service),
) -> AgentTrialReport:
    async def authorize_new() -> None:
        await authorize(request, principal.project_id)

    async def execute() -> AgentTrialReport:
        return await service.start(
            principal.project_id,
            principal.staff_id,
            agent_id,
            payload,
            authorize=authorize_new,
        )

    return await run_until_disconnect(execute, request)


@router.get("/{agent_id}/trials", response_model=list[AgentTrialReport])
async def list_trials(
    agent_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentTrialService = Depends(get_trial_service),
) -> list[AgentTrialReport]:
    return await service.list(
        principal.project_id, principal.staff_id, agent_id, limit
    )


@router.post("/{agent_id}/trials/lookup", response_model=AgentTrialReport)
async def match_trial_request(
    agent_id: UUID,
    payload: AgentTrialRequest,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentTrialService = Depends(get_trial_service),
) -> AgentTrialReport:
    # API can recover an unknown network result before reserving more quota,
    # while checking the original question without returning its raw digest.
    return await service.match_request(
        principal.project_id, principal.staff_id, agent_id, payload
    )


@router.get(
    "/{agent_id}/trials/requests/{request_key}",
    response_model=AgentTrialReport,
)
async def find_trial_request(
    agent_id: UUID,
    request_key: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentTrialService = Depends(get_trial_service),
) -> AgentTrialReport:
    return await service.by_request(
        principal.project_id, principal.staff_id, agent_id, request_key
    )


@router.get("/{agent_id}/trials/{report_id}", response_model=AgentTrialReport)
async def get_trial(
    agent_id: UUID,
    report_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentTrialService = Depends(get_trial_service),
) -> AgentTrialReport:
    return await service.get(
        principal.project_id, principal.staff_id, agent_id, report_id
    )


@router.post(
    "/{agent_id}/trials/{report_id}/cancel", response_model=AgentTrialReport
)
async def cancel_trial(
    agent_id: UUID,
    report_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentTrialService = Depends(get_trial_service),
) -> AgentTrialReport:
    return await service.cancel(
        principal.project_id, principal.staff_id, agent_id, report_id
    )
