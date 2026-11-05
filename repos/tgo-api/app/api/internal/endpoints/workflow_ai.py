"""Internal workflow model bridge with ownership read from Workflow."""

from fastapi import APIRouter, Depends

from app.api.internal.endpoints.workflow_approvals import (
    require_workflow_service,
)
from app.schemas.workflow_ai_usage import WorkflowAICommand, WorkflowAIResult
from app.services.workflow_ai_dispatch import run

router = APIRouter(dependencies=[Depends(require_workflow_service)])


@router.post("/run", response_model=WorkflowAIResult)
async def run_workflow_agent(command: WorkflowAICommand) -> WorkflowAIResult:
    return await run(command)
