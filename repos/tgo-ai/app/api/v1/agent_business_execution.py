"""Private business routes reject browser claimed permission."""

import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agent_trials import PrivateTrialRoute
from app.config import settings
from app.database import get_db
from app.schemas.agent_business_execution import (
    BusinessExecutionCheck,
    BusinessExecutionAuthority,
)
from app.services.agent_business_execution import AgentBusinessExecutionService
from app.services.agent_execution_channel import trusted_execution_channel
from app.schemas.workflow_business_query import (
    WorkflowBusinessQueryRequest, WorkflowBusinessQueryResult,
)
from app.services.workflow_business_query import WorkflowBusinessQueryService

router = APIRouter(route_class=PrivateTrialRoute)


@router.post(
    "/business-execution/authorize",
    response_model=BusinessExecutionAuthority,
)
async def authorize(
    check: BusinessExecutionCheck, request: Request,
    db: AsyncSession = Depends(get_db),
) -> BusinessExecutionAuthority:
    key = request.headers.get("X-Internal-API-Key", "")
    if not key or not hmac.compare_digest(key, settings.secret_key):
        raise HTTPException(403, "正式业务核验只允许业务服务访问")
    try:
        project = UUID(request.headers.get("X-Project-Id", ""))
    except ValueError:
        raise HTTPException(403, "业务企业范围无法确认") from None
    reference = check.command.reference
    if (
        project != reference.project_id
        or trusted_execution_channel(request, project) != reference.channel
    ):
        raise HTTPException(403, "原员工的业务渠道不一致")
    return await AgentBusinessExecutionService(db).authorize(check)


@router.post(
    "/business-execution/workflow-query",
    response_model=WorkflowBusinessQueryResult,
)
async def workflow_query(
    command: WorkflowBusinessQueryRequest, request: Request,
    db: AsyncSession = Depends(get_db),
) -> WorkflowBusinessQueryResult:
    key = request.headers.get("X-Internal-API-Key", "")
    if not key or not hmac.compare_digest(key, settings.secret_key):
        raise HTTPException(403, "工作流查询只允许内部服务访问")
    try:
        project = UUID(request.headers.get("X-Project-Id", ""))
    except ValueError:
        raise HTTPException(403, "工作流查询企业范围无法确认") from None
    if (
        project != command.project_id
        or trusted_execution_channel(request, project)
        != command.context.channel
    ):
        raise HTTPException(403, "工作流查询渠道或企业范围不一致")
    return await WorkflowBusinessQueryService(db).execute(command)
