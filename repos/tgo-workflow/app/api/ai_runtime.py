"""Internal metadata for the API-owned quota bridge; excluded from public DTOs."""
import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.ai_usage import WorkflowAIContext
from app.services.workflow_ai_context import read_context

router = APIRouter()


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
