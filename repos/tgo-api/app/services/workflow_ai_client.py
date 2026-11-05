"""Use the existing AI client without copying upstream private error bodies."""

import json
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.workflow_ai_usage import WorkflowAgentInput, WorkflowAIResult

if TYPE_CHECKING:
    from app.services.ai_client import AIServiceClient


async def run(
    client: "AIServiceClient",
    payload: WorkflowAgentInput,
    project_id: UUID,
    execution_id: UUID,
    lineage: tuple[str, ...],
) -> WorkflowAIResult:
    headers = client._execution_headers(str(project_id), "internal")
    if lineage:
        headers["X-Workflow-Parent-Execution-ID"] = str(execution_id)
        headers["X-Workflow-Lineage"] = json.dumps(lineage)
    response = await client._make_request(
        "POST",
        "/api/v1/agents/run",
        timeout=120,
        params={"project_id": str(project_id)},
        extra_headers=headers,
        json_data=payload.model_dump(mode="json", exclude_none=True),
    )
    if response.status_code != 200:
        raise HTTPException(response.status_code, "AI 员工调用未成功")
    try:
        return WorkflowAIResult.model_validate_json(response.content)
    except ValidationError:
        raise HTTPException(502, "AI 员工执行回执无法确认") from None
