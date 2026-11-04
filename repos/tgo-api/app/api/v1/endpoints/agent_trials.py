"""Administrator-only employee trials; identities come from the login session."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.security import require_admin
from app.models import Staff
from app.schemas.agent_knowledge_capabilities import AgentKnowledgeCapabilities
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.agent_trials import AgentTrialReport, AgentTrialRequest
from app.services import agent_trials
from app.services.request_execution import run_until_disconnect
from app.schemas.agent_trial_reviews import KnowledgeReviewRequest, KnowledgeReviewResponse, KnowledgeReviewState
from app.services import agent_trial_reviews
from app.api.v1.endpoints.agent_knowledge_cases import (
    router as knowledge_cases_router,
)

router = APIRouter()

router.include_router(knowledge_cases_router)


@router.get("/{agent_id}/knowledge-capabilities", response_model=AgentKnowledgeCapabilities)
async def knowledge_capabilities(
    agent_id: UUID, channel: KnowledgeChannel = Query(default=KnowledgeChannel.WEB),
    actor: Staff = Depends(require_admin()),
) -> AgentKnowledgeCapabilities:
    return await agent_trial_reviews.capabilities(actor.project_id, actor.id, agent_id, channel)


@router.post("/{agent_id}/trials/{report_id}/reviews", response_model=KnowledgeReviewResponse)
async def review_trial(
    agent_id: UUID, report_id: UUID, payload: KnowledgeReviewRequest,
    actor: Staff = Depends(require_admin()),
) -> KnowledgeReviewResponse:
    return await agent_trial_reviews.review(actor.project_id, actor.id, agent_id, report_id, payload)


@router.get("/{agent_id}/trials/{report_id}/reviews", response_model=KnowledgeReviewState)
async def trial_review_state(
    agent_id: UUID, report_id: UUID, actor: Staff = Depends(require_admin()),
) -> KnowledgeReviewState:
    return await agent_trial_reviews.state(actor.project_id, actor.id, agent_id, report_id)


@router.post("/{agent_id}/trials", response_model=AgentTrialReport)
async def start_trial(
    agent_id: UUID, payload: AgentTrialRequest, request: Request,
    actor: Staff = Depends(require_admin()),
) -> AgentTrialReport:
    async def execute() -> AgentTrialReport:
        return await agent_trials.start(actor.project_id, actor.id, agent_id, payload)

    return await run_until_disconnect(execute, request)


@router.get("/{agent_id}/trials", response_model=list[AgentTrialReport])
async def list_trials(
    agent_id: UUID, limit: int = Query(default=20, ge=1, le=100),
    actor: Staff = Depends(require_admin()),
) -> list[AgentTrialReport]:
    return await agent_trials.list_reports(actor.project_id, actor.id, agent_id, limit)


@router.post("/{agent_id}/trials/lookup", response_model=AgentTrialReport)
async def lookup_trial(
    agent_id: UUID, payload: AgentTrialRequest, actor: Staff = Depends(require_admin()),
) -> AgentTrialReport:
    return await agent_trials.lookup(actor.project_id, actor.id, agent_id, payload)


@router.get("/{agent_id}/trials/requests/{request_key}", response_model=AgentTrialReport)
async def find_trial_request(
    agent_id: UUID, request_key: UUID, actor: Staff = Depends(require_admin()),
) -> AgentTrialReport:
    return await agent_trials.by_request(actor.project_id, actor.id, agent_id, request_key)


@router.get("/{agent_id}/trials/{report_id}", response_model=AgentTrialReport)
async def get_trial(
    agent_id: UUID, report_id: UUID, actor: Staff = Depends(require_admin()),
) -> AgentTrialReport:
    return await agent_trials.get(actor.project_id, actor.id, agent_id, report_id)


@router.post("/{agent_id}/trials/{report_id}/cancel", response_model=AgentTrialReport)
async def cancel_trial(
    agent_id: UUID, report_id: UUID, actor: Staff = Depends(require_admin()),
) -> AgentTrialReport:
    return await agent_trials.cancel(actor.project_id, actor.id, agent_id, report_id)
