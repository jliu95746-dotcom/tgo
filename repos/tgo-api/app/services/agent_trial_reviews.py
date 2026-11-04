"""The gateway owns definitions; callers supply human judgments."""
from uuid import UUID
from fastapi import HTTPException

from app.schemas.agent_trial_reviews import (
    KnowledgeReviewCommand,
    KnowledgeReviewQuery,
    KnowledgeReviewRequest,
    KnowledgeReviewResponse,
    KnowledgeReviewState,
)
from app.services.ai_client import ai_client
from app.services import agent_knowledge_cases
from app.schemas.agent_knowledge_capabilities import (
    AgentKnowledgeCapabilities,
    KnowledgeCapabilityQuery,
)
from app.schemas.knowledge import KnowledgeChannel


async def capabilities(
    project: UUID, reviewer: UUID, agent: UUID, channel: KnowledgeChannel
) -> AgentKnowledgeCapabilities:
    catalog = await agent_knowledge_cases.read(project, reviewer, agent)
    result = await ai_client.agent_knowledge_capabilities(
        project,
        reviewer,
        agent,
        KnowledgeCapabilityQuery(cases=catalog.cases, channel=channel),
    )
    if (
        await agent_knowledge_cases.read(project, reviewer, agent)
    ).id != catalog.id:
        raise HTTPException(409, "测试问题在检查期间发生变化，请重新读取")
    return result


async def review(
    project: UUID,
    reviewer: UUID,
    agent: UUID,
    report: UUID,
    request: KnowledgeReviewRequest,
) -> KnowledgeReviewResponse:
    catalog = await agent_knowledge_cases.read(project, reviewer, agent)
    command = KnowledgeReviewCommand(
        **request.model_dump(),
        case=await agent_knowledge_cases.case(
            project, reviewer, agent, request.case_id
        ),
        current_cases=catalog.cases,
    )
    receipt = await ai_client.review_agent_trial(
        project, reviewer, agent, report, command
    )
    current = await agent_knowledge_cases.read(project, reviewer, agent)
    if receipt.case not in current.cases:
        receipt.case_status, receipt.effective = "changed", False
    return receipt


async def state(
    project: UUID, reviewer: UUID, agent: UUID, report: UUID
) -> KnowledgeReviewState:
    catalog = await agent_knowledge_cases.read(project, reviewer, agent)
    result = await ai_client.agent_trial_review_state(
        project,
        reviewer,
        agent,
        report,
        KnowledgeReviewQuery(cases=catalog.cases),
    )
    if (
        await agent_knowledge_cases.read(project, reviewer, agent)
    ).id != catalog.id:
        raise HTTPException(409, "测试问题在检查期间发生变化，请重新读取")
    return result
