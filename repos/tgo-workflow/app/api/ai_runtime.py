"""Private metadata and results for the API-owned quota bridge."""
import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.ai_usage import (
    WorkflowAIContext,
    WorkflowAICacheCommand,
    WorkflowAICacheWrite,
    WorkflowAICacheReceipt,
)
from app.services.workflow_ai_context import read_context
from app.services import workflow_ai_results

router = APIRouter()


def require_result_service(
    x_internal_api_key: str | None = Header(None),
) -> None:
    secret = settings.SECRET_KEY
    if (
        secret is None
        or not x_internal_api_key
        or not hmac.compare_digest(
            x_internal_api_key, secret.get_secret_value()
        )
    ):
        raise HTTPException(403, "内部服务身份无效")


@router.post(
    "/ai-runtime/results/prepare",
    response_model=WorkflowAICacheReceipt,
    dependencies=[Depends(require_result_service)],
)
async def prepare_ai_result(
    request: WorkflowAICacheCommand,
    db: AsyncSession = Depends(get_db),
) -> WorkflowAICacheReceipt:
    return await workflow_ai_results.prepare(db, request)


@router.post(
    "/ai-runtime/results/read",
    response_model=WorkflowAICacheReceipt,
    dependencies=[Depends(require_result_service)],
)
async def read_ai_result(
    request: WorkflowAICacheCommand,
    db: AsyncSession = Depends(get_db),
) -> WorkflowAICacheReceipt:
    return await workflow_ai_results.read(db, request)


@router.post(
    "/ai-runtime/results/store",
    response_model=WorkflowAICacheReceipt,
    dependencies=[Depends(require_result_service)],
)
async def store_ai_result(
    request: WorkflowAICacheWrite,
    db: AsyncSession = Depends(get_db),
) -> WorkflowAICacheReceipt:
    return await workflow_ai_results.store(db, request)


@router.get(
    "/executions/{execution_id}/ai-context", response_model=WorkflowAIContext
)
async def workflow_ai_context(
    execution_id: UUID,
    project_id: UUID = Query(...),
    node_id: str = Query(..., min_length=1, max_length=128),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> WorkflowAIContext:
    secret = settings.SECRET_KEY
    if (
        secret is None
        or not x_internal_api_key
        or not hmac.compare_digest(
            x_internal_api_key, secret.get_secret_value()
        )
    ):
        raise HTTPException(403, "内部服务身份无效")
    return await read_context(db, project_id, execution_id, node_id)
