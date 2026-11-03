"""The gateway selects definitions; the caller supplies only human judgments."""
from uuid import UUID

from app.schemas.agent_trial_reviews import (
    KnowledgeReviewCommand, KnowledgeReviewQuery, KnowledgeReviewRequest, KnowledgeReviewResponse, KnowledgeReviewState,
)
from app.services.ai_client import ai_client
from app.services.knowledge_review_cases import get_case, list_cases
from app.schemas.agent_knowledge_capabilities import AgentKnowledgeCapabilities, KnowledgeCapabilityQuery
from app.schemas.knowledge import KnowledgeChannel


async def capabilities(project: UUID, reviewer: UUID, agent: UUID, channel: KnowledgeChannel) -> AgentKnowledgeCapabilities:
    return await ai_client.agent_knowledge_capabilities(project, reviewer, agent,
        KnowledgeCapabilityQuery(cases=list_cases(), channel=channel))


async def review(project: UUID, reviewer: UUID, agent: UUID, report: UUID, request: KnowledgeReviewRequest) -> KnowledgeReviewResponse:
    command = KnowledgeReviewCommand(**request.model_dump(), case=get_case(request.case_id))
    return await ai_client.review_agent_trial(project, reviewer, agent, report, command)


async def state(project: UUID, reviewer: UUID, agent: UUID, report: UUID) -> KnowledgeReviewState:
    return await ai_client.agent_trial_review_state(project, reviewer, agent, report, KnowledgeReviewQuery(cases=list_cases()))
