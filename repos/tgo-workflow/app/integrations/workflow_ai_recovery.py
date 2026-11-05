"""One generation request, followed only by a bounded original-result read."""
import httpx
from pydantic import JsonValue

from app.integrations.ai_errors import WorkflowAIError
from app.schemas.ai_usage import WorkflowAICommand, WorkflowAIResult
from app.schemas.agent_runtime import AgentRuntimeResult


def _result(response: httpx.Response) -> AgentRuntimeResult:
    saved = WorkflowAIResult.model_validate_json(response.content)
    return AgentRuntimeResult(
        success=True,
        content=saved.content,
        execution_id=saved.execution_id,
    )


async def _read_original(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    payload: dict[str, JsonValue],
) -> AgentRuntimeResult:
    try:
        response = await client.post(
            base_url.rstrip("/") + "/internal/workflow-ai/recover",
            json=payload,
            headers=headers,
            timeout=15.0,
        )
    except (httpx.HTTPError, OSError, TimeoutError):
        raise WorkflowAIError(503) from None
    if response.status_code != 200:
        status = response.status_code
        raise WorkflowAIError(409 if status in {404, 409, 410} else status)
    try:
        return _result(response)
    except ValueError:
        raise WorkflowAIError(503) from None


async def run_with_recovery(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    command: WorkflowAICommand,
) -> AgentRuntimeResult:
    payload = command.model_dump(mode="json", exclude_none=True)
    try:
        response = await client.post(
            base_url.rstrip("/") + "/internal/workflow-ai/run",
            json=payload,
            headers=headers,
            timeout=120.0,
        )
    except (httpx.HTTPError, OSError, TimeoutError):
        return await _read_original(client, base_url, headers, payload)
    if response.status_code != 200:
        if response.status_code >= 500 or response.status_code in {408, 409}:
            return await _read_original(client, base_url, headers, payload)
        raise WorkflowAIError(response.status_code)
    try:
        return _result(response)
    except ValueError:
        return await _read_original(client, base_url, headers, payload)
