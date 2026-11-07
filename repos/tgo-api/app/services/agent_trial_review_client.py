"Review transport delegates to the existing AI client and trusted \
identity."
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.agent_trial_reviews import (
    KnowledgeReviewCommand,
    KnowledgeReviewQuery,
    KnowledgeReviewResponse,
    KnowledgeReviewState,
)
from app.schemas.agent_knowledge_capabilities import (
    AgentKnowledgeCapabilities,
    KnowledgeCapabilityQuery,
)

if TYPE_CHECKING:
    from app.services.ai_client import AIServiceClient


async def capabilities(
    client: "AIServiceClient",
    project: UUID,
    reviewer: UUID,
    agent: UUID,
    query: KnowledgeCapabilityQuery,
) -> AgentKnowledgeCapabilities:
    response = await client._make_request(
        "POST",
        f"/api/v1/agents/{agent}/knowledge-capabilities",
        json_data=query.model_dump(mode="json"),
        extra_headers=client._trial_headers(project, reviewer),
    )
    if not response.is_success:
        raise HTTPException(response.status_code, "无法读取能力状态，请刷新后重试")
    try:
        result = AgentKnowledgeCapabilities.model_validate_json(
            response.content
        )
    except ValidationError:
        raise HTTPException(502, "能力状态服务返回了无效结果") from None
    received = [
        row.case for group in result.capabilities for row in group.cases
    ]
    expected = {case.id: case for case in query.cases}
    if (
        result.agent_id != agent
        or result.reviewed_by != reviewer
        or result.channel != query.channel
        or len(received) != len(expected)
        or len({case.id for case in received}) != len(expected)
        or any(expected.get(case.id) != case for case in received)
    ):
        raise HTTPException(502, "能力状态与当前员工或用例不一致")
    return result


async def submit(
    client: "AIServiceClient",
    project: UUID,
    reviewer: UUID,
    agent: UUID,
    report: UUID,
    command: KnowledgeReviewCommand,
) -> KnowledgeReviewResponse:
    response = await client._make_request(
        "POST",
        f"/api/v1/agents/{agent}/trials/{report}/reviews",
        json_data=command.model_dump(mode="json"),
        extra_headers=client._trial_headers(project, reviewer),
    )
    if not response.is_success:
        raise HTTPException(response.status_code, "评审未完成，请查询原记录或重新试聊")
    try:
        receipt = KnowledgeReviewResponse.model_validate_json(response.content)
    except ValidationError:
        raise HTTPException(502, "评审服务返回了无效结果") from None
    if (
        receipt.agent_id != agent
        or receipt.report_id != report
        or receipt.reviewed_by != reviewer
        or receipt.request_key != command.request_key
        or receipt.case.id != command.case_id
        or receipt.decision != command.decision
        or receipt.case != command.case
        or receipt.checks != command.checks
    ):
        raise HTTPException(502, "评审结果与原请求不一致")
    return receipt


async def state(
    client: "AIServiceClient",
    project: UUID,
    reviewer: UUID,
    agent: UUID,
    report: UUID,
    query: KnowledgeReviewQuery,
) -> KnowledgeReviewState:
    response = await client._make_request(
        "POST",
        f"/api/v1/agents/{agent}/trials/{report}/review-state",
        json_data=query.model_dump(mode="json"),
        extra_headers=client._trial_headers(project, reviewer),
    )
    if not response.is_success:
        raise HTTPException(response.status_code, "评审记录暂不可读，请刷新后重试")
    try:
        result = KnowledgeReviewState.model_validate_json(response.content)
    except ValidationError:
        raise HTTPException(502, "评审服务返回了无效记录") from None
    if (
        result.agent_id != agent
        or result.report_id != report
        or any(
            row.agent_id != agent
            or row.report_id != report
            or row.reviewed_by != reviewer
            for row in result.reviews
        )
    ):
        raise HTTPException(502, "评审记录标识不一致")
    current_cases = {case.id: case for case in query.cases}
    if any(
        current_cases.get(case.id) != case for case in result.available_cases
    ):
        raise HTTPException(502, "评审用例与当前模板不一致")
    return result
