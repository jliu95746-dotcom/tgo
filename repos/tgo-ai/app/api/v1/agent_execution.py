"""Private publication recheck: a past model response does not grant access."""
import hmac
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agent_trials import PrivateTrialRoute
from app.config import settings
from app.database import get_db
from app.schemas.agent_execution import (
    AgentExecutionCheckReceipt,
    AgentExecutionReference,
)
from app.schemas.workflow_binding_check import (
    WorkflowBindingCheckReceipt,
    WorkflowBindingCheckRequest,
)
from app.services.agent_execution_channel import trusted_execution_channel
from app.services.agent_execution_reference import read_reference
from app.services.workflow_execution_authorization import (
    require_current_binding,
)

router = APIRouter(route_class=PrivateTrialRoute)


@router.get(
    "/execution-reference/{agent_id}",
    response_model=AgentExecutionReference,
)
async def capture_execution_reference(
    agent_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AgentExecutionReference:
    """Capture the current execution reference for a reviewed answer."""
    try:
        project = UUID(request.headers.get("X-Project-Id", ""))
    except ValueError:
        raise HTTPException(403, "执行核验企业身份无法确认") from None
    channel = trusted_execution_channel(request, project)
    if channel is None:
        raise HTTPException(403, "执行核验只允许业务服务访问")
    return await read_reference(db, project, agent_id, channel)


@router.post(
    "/workflow-binding-check", response_model=WorkflowBindingCheckReceipt
)
async def check_workflow_binding(
    reference: WorkflowBindingCheckRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WorkflowBindingCheckReceipt:
    """Recheck a fixed employee binding immediately before workflow acceptance."""
    key = request.headers.get("X-Internal-API-Key", "")
    if not key or not hmac.compare_digest(key, settings.secret_key):
        raise HTTPException(403, "员工工作流核验只允许内部服务访问")
    try:
        project = UUID(request.headers.get("X-Project-Id", ""))
    except ValueError:
        raise HTTPException(403, "员工工作流企业身份无法确认") from None
    if reference.project_id != project:
        raise HTTPException(403, "员工工作流企业范围不一致")
    try:
        await require_current_binding(
            db,
            str(reference.agent_id),
            str(project),
            reference.workflow_id,
            reference.release_version,
            reference.release_id,
            reference.release_digest,
        )
    except PermissionError:
        raise HTTPException(409, "员工工作流授权已变更，停止执行") from None
    return WorkflowBindingCheckReceipt()


@router.post("/execution-check", response_model=AgentExecutionCheckReceipt)
async def check_execution(
    reference: AgentExecutionReference,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AgentExecutionCheckReceipt:
    key = request.headers.get("X-Internal-API-Key", "")
    if not key or not hmac.compare_digest(key, settings.secret_key):
        raise HTTPException(403, "执行核验只允许业务服务访问")
    try:
        project = UUID(request.headers.get("X-Project-Id", ""))
    except ValueError:
        raise HTTPException(403, "执行核验企业身份无法确认") from None
    channel = trusted_execution_channel(request, project)
    if reference.project_id != project or reference.channel != channel:
        raise HTTPException(403, "原执行与当前业务服务范围不一致")
    current = await read_reference(db, project, reference.agent_id, channel)
    if current != reference:
        raise HTTPException(409, "原执行的配置或授权已变化，请重新核验或转人工")
    return AgentExecutionCheckReceipt(
        reference=current, checked_at=datetime.now(UTC)
    )
