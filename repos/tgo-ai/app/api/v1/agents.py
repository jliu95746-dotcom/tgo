"""Agent management API endpoints."""

import uuid
import json
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.services.quota_authorization import require_reply_quota
from app.core.logging import get_logger
from app.dependencies import (
    get_agent_service,
    get_pagination_params,
    get_supervisor_runtime_service,
)

from app.schemas.agent import (
    AgentCreate,
    AgentListResponse,
    AgentResponse,
    AgentUpdate,
    AgentWithDetails,
    ToggleEnabledRequest,
)
from app.schemas.base import PaginationMetadata
from app.api.responses import ResponseDefinitions, build_error_responses
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.services.agent_service import AgentService
from app.api.v1.agent_mutation_principal import optional_mutation_actor
from app.api.v1.agent_trials import TrialPrincipal, require_trial_principal
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.knowledge_availability import AgentKnowledgeAvailability
from app.schemas.device_access import AgentDeviceActionReadiness
from app.services.default_knowledge import agent_knowledge_availability

from app.schemas.agent_run import SupervisorRunRequest, SupervisorRunResponse
from app.schemas.workflow_release_audit import WorkflowReleaseAuditResponse
from app.services.workflow_release_audit import list_unpinned_workflows
from app.schemas.workflow_pin_transition import (
    WorkflowPinTransitionCommand,
    WorkflowPinTransitionHistory,
    WorkflowPinTransitionReceipt,
)
from app.services.workflow_pin_transition import WorkflowPinTransitionService
from app.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.agent_execution_channel import trusted_execution_channel
from app.services.workflow_parent_context import trusted_workflow_parent

logger = get_logger(__name__)


class CancelRunRequest(BaseModel):
    reason: Optional[str] = Field(
        default=None,
        description="Optional reason for cancellation (for auditing/logs)",
    )


router = APIRouter()


@router.get(
    "/knowledge-availability", response_model=AgentKnowledgeAvailability
)
async def get_agent_knowledge_availability(
    project_id: uuid.UUID = Query(...),
    channel: KnowledgeChannel = Query(...),
    agent_id: uuid.UUID | None = Query(None),
    agent_service: AgentService = Depends(get_agent_service),
) -> AgentKnowledgeAvailability:
    if agent_id:
        agent = await agent_service.get_agent(
            project_id, agent_id, enrich_resources=False
        )
    else:
        agent = await agent_service.get_default_agent(
            project_id, enrich_resources=False
        )
    return await agent_knowledge_availability(agent, channel)


_STREAMING_EXAMPLE = (
    'event: connected\n'
    'data: {"message": "Stream connected", "request_id": "req-123", '
    '"correlation_id": "corr-456"}\n'
    '\n'
    'event: event\n'
    'data: {"event_type": "workflow_started", "timestamp": '
    '"2024-05-01T12:00:00Z", "correlation_id": "corr-456", "request_id": '
    '"req-123", "severity": "info", "data": {"request_id": "req-123", '
    '"agent_id": "agent-001", "agent_name": "Support Agent", '
    '"session_id": "sess-1", "message_length": 128}, "metadata": '
    '{"phase": "initialization", "agent_id": "agent-001", "session_id": '
    '"sess-1"}}\n'
    '\n'
    'event: event\n'
    'data: {"event_type": "agent_execution_started", "timestamp": '
    '"2024-05-01T12:00:01Z", "correlation_id": "corr-456", "request_id": '
    '"req-123", "severity": "info", "data": {"agent_id": "agent-001", '
    '"agent_name": "Support Agent", "execution_id": "run-abc", '
    '"question": "How do I fix this error?"}, "metadata": {"phase": '
    '"execution", "step": 3}}\n'
    '\n'
    'event: event\n'
    'data: {"event_type": "agent_content_chunk", "timestamp": '
    '"2024-05-01T12:00:02Z", "correlation_id": "corr-456", "request_id": '
    '"req-123", "severity": "info", "data": {"agent_id": "agent-001", '
    '"agent_name": "Support Agent", "execution_id": "run-abc", '
    '"content_chunk": "Investigating...", "chunk_index": 0, "is_final": '
    'false}, "metadata": {"phase": "agent_execution", "agent_id": '
    '"agent-001"}}\n'
    '\n'
    'event: event\n'
    'data: {"event_type": "agent_tool_call_started", "timestamp": '
    '"2024-05-01T12:00:03Z", "correlation_id": "corr-456", "request_id": '
    '"req-123", "severity": "info", "data": {"agent_id": "agent-001", '
    '"agent_name": "Support Agent", "execution_id": "run-abc", '
    '"tool_name": "search", "tool_call_id": "tc_01", "tool_input": '
    '{"query": "error logs"}, "status": "started"}, "metadata": '
    '{"phase": "agent_execution", "agent_id": "agent-001", "tool_name": '
    '"search"}}\n'
    '\n'
    'event: event\n'
    'data: {"event_type": "agent_tool_call_completed", "timestamp": '
    '"2024-05-01T12:00:04Z", "correlation_id": "corr-456", "request_id": '
    '"req-123", "severity": "success", "data": {"agent_id": "agent-001", '
    '"agent_name": "Support Agent", "execution_id": "run-abc", '
    '"tool_name": "search", "tool_call_id": "tc_01", "tool_input": '
    '{"query": "error logs"}, "tool_output": "3 results", "status": '
    '"completed"}, "metadata": {"phase": "agent_execution", "agent_id": '
    '"agent-001", "tool_name": "search"}}\n'
    '\n'
    'event: event\n'
    'data: {"event_type": "agent_response_complete", "timestamp": '
    '"2024-05-01T12:00:05Z", "correlation_id": "corr-456", "request_id": '
    '"req-123", "severity": "success", "data": {"agent_id": "agent-001", '
    '"agent_name": "Support Agent", "execution_id": "run-abc", '
    '"final_content": "Please restart the worker and retry.", "success": '
    'true, "total_chunks": 1, "tool_calls_count": 1, "response_length": '
    '35}, "metadata": {"phase": "agent_execution", "agent_id": '
    '"agent-001"}}\n'
    '\n'
    'event: event\n'
    'data: {"event_type": "workflow_completed", "timestamp": '
    '"2024-05-01T12:00:08Z", "correlation_id": "corr-456", "request_id": '
    '"req-123", "severity": "success", "data": {"phase": "completed", '
    '"progress_percentage": 100.0, "current_step": "Workflow completed", '
    '"total_steps": 4, "completed_steps": 4}, "metadata": '
    '{"total_execution_time": 8.0, "agents_consulted": 1}}\n'
    '\n'
    'event: disconnected\n'
    'data: {"message": "Stream disconnected"}\n'
    '\n'
)


_run_success_responses: ResponseDefinitions = {
    200: {
        "description": (
            'Successful response. Returns JSON when `stream=false` and '
            'Server-Sent Events when `stream=true`.'
        ),
        "content": {
            "application/json": {
                "schema": {
                    "$ref": "#/components/schemas/SupervisorRunResponse",
                },
            },
            "text/event-stream": {
                "schema": {
                    "type": "string",
                    "description": (
                        'Server-Sent Events (SSE) stream. Event names: '
                        'connected | event | error | disconnected. Domain '
                        "events use 'event' with a StreamingEvent JSON "
                        'payload.'
                    ),
                },
                "examples": {
                    "agent_execution_started": {
                        "summary": "Agent execution started (domain event)",
                        "value": (
                            'event: event\n'
                            'data: {"event_type": "agent_execution_started", '
                            '"timestamp": "2024-05-01T12:00:01Z", '
                            '"correlation_id": "corr-456", "request_id": '
                            '"req-123", "severity": "info", "data": '
                            '{"agent_id": "agent-001", "agent_name": '
                            '"Support Agent", "execution_id": "run-abc", '
                            '"question": "How do I fix this error?"}, '
                            '"metadata": {"phase": "execution", "step": 3}}\n'
                            '\n'
                        ),
                    },
                    "agent_content_chunk": {
                        "summary": "Agent content chunk (domain event)",
                        "value": (
                            'event: event\n'
                            'data: {"event_type": "agent_content_chunk", '
                            '"timestamp": "2024-05-01T12:00:02Z", '
                            '"correlation_id": "corr-456", "request_id": '
                            '"req-123", "severity": "info", "data": '
                            '{"agent_id": "agent-001", "agent_name": '
                            '"Support Agent", "execution_id": "run-abc", '
                            '"content_chunk": "Found 3 docs...", '
                            '"chunk_index": 0, "is_final": false}, '
                            '"metadata": {"phase": "agent_execution", '
                            '"agent_id": "agent-001"}}\n'
                            '\n'
                        ),
                    },
                    "agent_tool_call_started": {
                        "summary": "Agent tool call started (domain event)",
                        "value": (
                            'event: event\n'
                            'data: {"event_type": "agent_tool_call_started", '
                            '"timestamp": "2024-05-01T12:00:03Z", '
                            '"correlation_id": "corr-456", "request_id": '
                            '"req-123", "severity": "info", "data": '
                            '{"agent_id": "agent-001", "agent_name": '
                            '"Support Agent", "execution_id": "run-abc", '
                            '"tool_name": "search", "tool_call_id": "tc_01", '
                            '"tool_input": {"query": "..."}, "status": '
                            '"started"}, "metadata": {"phase": '
                            '"agent_execution", "agent_id": "agent-001", '
                            '"tool_name": "search"}}\n'
                            '\n'
                        ),
                    },
                    "agent_tool_call_completed": {
                        "summary": "Agent tool call completed (domain event)",
                        "value": (
                            'event: event\n'
                            'data: {"event_type": "agent_tool_call_completed"'
                            ', "timestamp": "2024-05-01T12:00:04Z", '
                            '"correlation_id": "corr-456", "request_id": '
                            '"req-123", "severity": "success", "data": '
                            '{"agent_id": "agent-001", "agent_name": '
                            '"Support Agent", "execution_id": "run-abc", '
                            '"tool_name": "search", "tool_call_id": "tc_01", '
                            '"tool_input": {"query": "..."}, "tool_output": '
                            '"3 results", "status": "completed"}, '
                            '"metadata": {"phase": "agent_execution", '
                            '"agent_id": "agent-001", "tool_name": '
                            '"search"}}\n'
                            '\n'
                        ),
                    },
                    "agent_response_complete": {
                        "summary": "Agent response complete (domain event)",
                        "value": (
                            'event: event\n'
                            'data: {"event_type": "agent_response_complete", '
                            '"timestamp": "2024-05-01T12:00:05Z", '
                            '"correlation_id": "corr-456", "request_id": '
                            '"req-123", "severity": "success", "data": '
                            '{"agent_id": "agent-001", "agent_name": '
                            '"Support Agent", "execution_id": "run-abc", '
                            '"final_content": "Resolved output", "success": '
                            'true, "total_chunks": 1, "tool_calls_count": 1, '
                            '"response_length": 15}, "metadata": {"phase": '
                            '"agent_execution", "agent_id": "agent-001"}}\n'
                            '\n'
                        ),
                    },
                    "error_event": {
                        "summary": "SSE transport error",
                        "value": (
                            'event: error\n'
                            'data: {"message": "Stream error occurred", '
                            '"error": "TimeoutError"}\n'
                            '\n'
                        ),
                    },
                    "domain_workflow_completed": {
                        "summary": "Workflow completed (domain event)",
                        "value": (
                            'event: event\n'
                            'data: {"event_type": "workflow_completed", '
                            '"timestamp": "2024-05-01T12:00:05Z", '
                            '"correlation_id": "corr-456", "request_id": '
                            '"req-123", "severity": "success", "data": '
                            '{"phase": "completed", "progress_percentage": '
                            '100.0, "current_step": "Workflow completed", '
                            '"total_steps": 4, "completed_steps": 4}, '
                            '"metadata": {"total_execution_time": 5.0, '
                            '"agents_consulted": 1}}\n'
                            '\n'
                        ),
                    },
                    "full_sequence": {
                        "summary": "Full sequence example",
                        "value": _STREAMING_EXAMPLE,
                    },
                },
            },
        },
    }
}


@router.post(
    "/run",
    dependencies=[Depends(require_reply_quota)],
    response_model=SupervisorRunResponse,
    responses={
        **_run_success_responses,
        **build_error_responses(
            [400, 404, 429, 500],
            {
                404: "Agent resource not found",
                429: "Agent service rate limit exceeded",
                500: "Agent service unavailable",
            },
        ),
    },
)
async def run_supervisor_agent(
    payload: SupervisorRunRequest,
    request: Request,
    project_id: uuid.UUID = Query(..., description="Project ID"),
    runtime_service: SupervisorRuntimeService = Depends(
        get_supervisor_runtime_service
    ),
) -> Union[SupervisorRunResponse, StreamingResponse]:
    (
        'Run the supervisor agent.\n'
        '\n'
        '    Response content types:\n'
        '    - application/json: Returned when `payload.stream=false`.\n'
        '    - text/event-stream (SSE): Returned when `payload.stream=true`.\n'
        '\n'
        '    Server-Sent Events (SSE):\n'
        '    - Framing: Each SSE is formatted as `event: <type>` then `data: '
        '<json>` followed by a blank line.\n'
        '    - SSE event names used by this endpoint:\n'
        '      - connected: initial connection info\n'
        '      - event: domain events encoded as a StreamingEvent JSON '
        'object (see structure below)\n'
        '      - error: transport/stream errors\n'
        '      - disconnected: stream closed\n'
        "    - StreamingEvent JSON structure (payload of the 'event' SSE):\n"
        '      {\n'
        '        "event_type": "<EventType>",\n'
        '        "timestamp": "RFC3339",\n'
        '        "correlation_id": "string",\n'
        '        "request_id": "string",\n'
        '        "severity": "info|warning|error|success",\n'
        '        "data": { ... event-specific fields ... },\n'
        '        "metadata": { ... optional key/value metadata ... }\n'
        '      }\n'
        '    - Representative event_type values (see app/models/streaming.py '
        'for the full list):\n'
        '      - workflow_started, workflow_completed, workflow_failed\n'
        '      - agent_execution_started\n'
        '      - agent_content_chunk, agent_tool_call_started, '
        'agent_tool_call_completed, agent_response_complete\n'
        '    - Representative event data models (payloads in '
        'StreamingEvent.data):\n'
        '      - WorkflowStartedData: request_id, agent_id, agent_name, '
        'session_id?, message_length\n'
        '      - AgentExecutionData: agent_id, agent_name, execution_id, '
        'question, execution_time?, success?, error?, response_length?\n'
        '      - AgentContentChunkData: agent_id, agent_name, execution_id, '
        'content_chunk, chunk_index, is_final\n'
        '      - AgentToolCallData: agent_id, agent_name, execution_id, '
        'tool_name, tool_call_id?, tool_input?, tool_output?, status, error?\n'
        '      - AgentResponseCompleteData: agent_id, agent_name, '
        'execution_id, final_content, success, total_chunks, '
        'tool_calls_count, response_length\n'
        '\n'
        '    Error handling:\n'
        '    - Operational errors during streaming are emitted as an `error` '
        'event and the stream terminates.\n'
        '    - If validation errors occur before streaming starts, the '
        'response is a non-200 JSON error with an appropriate HTTP status '
        '(e.g., 400/404/429/500).\n'
        '\n'
        '    Typical event sequence:\n'
        '    1) connected (informational)\n'
        '    2) event(workflow_started)\n'
        '    3) zero or more domain events, including:\n'
        '       - agent_execution_started\n'
        '       - agent_content_chunk\n'
        '       - agent_tool_call_started / agent_tool_call_completed\n'
        '       - agent_response_complete\n'
        '\n'
        '    4) event(workflow_completed) on success OR '
        'event(workflow_failed) on failure\n'
        '    5) disconnected (stream ends)\n'
        '\n'
        '    Error handling:\n'
        '    - Operational/transport errors during streaming may be emitted '
        'as an `error` SSE and the stream terminates.\n'
        '    - Domain failures are emitted as an `event` SSE whose payload '
        'has `event_type: workflow_failed` (severity: error), then the '
        'stream closes.\n'
        '\n'
        '    Client guidance:\n'
        "    - JavaScript: use EventSource and listen to 'event' to handle "
        'domain events; parse `event_type` from JSON.\n'
        '      const es = new EventSource(url);\n'
        "      es.addEventListener('event', (e) => {\n"
        '        const evt = JSON.parse(e.data);\n'
        '        switch (evt.event_type) {\n'
        "          case 'agent_execution_started': /* initialize "
        'single-agent UI */ break;\n'
        "          case 'agent_content_chunk': /* handle text chunk */ "
        'break;\n'
        "          case 'agent_tool_call_started': /* ... */ break;\n"
        "          case 'agent_tool_call_completed': /* ... */ break;\n"
        "          case 'agent_response_complete': /* finalize agent output "
        '*/ break;\n'
        "          case 'workflow_completed': es.close(); break;\n"
        "          case 'workflow_failed': es.close(); break;\n"
        '          default: /* optionally log unknown types */ break;\n'
        '        }\n'
        '      });\n'
        "      es.addEventListener('connected', () => { /* optional: "
        'connection ack */ });\n'
        "      es.addEventListener('error', () => { /* close or retry if "
        'desired */ });\n'
        "      es.addEventListener('disconnected', () => { /* stream closed "
        '*/ });\n'
        '    - Python: use an SSE client (e.g., sseclient or httpx stream) '
        'and parse `event:` and `data:` lines, reading `event_type` from '
        'JSON.\n'
        '\n'
        '    Notes:\n'
        "    - Completion can be detected via the 'disconnected' SSE or by "
        'seeing domain events `workflow_completed`/`workflow_failed`.\n'
        '    - For reconnection, carry over `correlation_id`/`request_id` if '
        'supported by your integration.\n'
        '    '
    )

    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

    # Build headers for downstream services (no authentication)
    extra_headers = {
        "X-Project-ID": str(project_id),
    }
    channel = trusted_execution_channel(request, project_id)
    workflow_parent = trusted_workflow_parent(request, channel)
    if workflow_parent is not None:
        extra_headers["X-Workflow-Parent-Execution-ID"] = str(
            workflow_parent.parent_execution_id
        )
        extra_headers["X-Workflow-Lineage"] = json.dumps(
            workflow_parent.workflow_lineage
        )
    business_context = request.headers.get("X-Business-Request-Context")
    business_request = request.headers.get("X-Business-Request-ID")
    if business_context is not None or business_request is not None:
        if channel is None or not business_context or not business_request:
            raise HTTPException(403, "业务客户上下文必须由业务服务确认")
        extra_headers["X-Business-Request-Context"] = business_context
        extra_headers["X-Business-Request-ID"] = business_request
    if channel is not None:
        if (
            payload.knowledge_channel is not None
            and payload.knowledge_channel != channel
        ):
            raise HTTPException(403, "知识渠道与服务端执行渠道不一致")
        extra_headers["X-Execution-Channel"] = channel.value
        payload = payload.model_copy(update={"knowledge_channel": channel})
    if payload.rag_url is None:
        payload = payload.model_copy(
            update={"rag_url": settings.rag_service_url}
        )

    if payload.mcp_url is None:
        payload = payload.model_copy(
            update={"mcp_url": settings.mcp_service_url}
        )

    if payload.stream:
        sse_response = await runtime_service.stream(
            payload,
            project_id,
            extra_headers={**extra_headers, "X-Request-ID": request_id},
            http_request=request,
        )
        return sse_response

    response = await runtime_service.run(
        payload,
        project_id,
        extra_headers={**extra_headers, "X-Request-ID": request_id},
        http_request=request,
    )
    return response


@router.post(
    "/run/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    responses=build_error_responses([500]),
)
async def cancel_supervisor_run(
    run_id: str,
    payload: Optional[CancelRunRequest] = None,
    project_id: uuid.UUID = Query(..., description="Project ID"),
    runtime_service: SupervisorRuntimeService = Depends(
        get_supervisor_runtime_service
    ),
) -> dict:
    (
        'Cancel a running supervisor agent execution by run_id.\n'
        '\n'
        '    Always returns 202 Accepted. Body indicates whether a cancel '
        'signal was issued.\n'
        '    '
    )
    reason = payload.reason if payload else None
    cancelled = await runtime_service.cancel(run_id, project_id, reason=reason)
    return {"run_id": run_id, "cancelled": cancelled, "reason": reason}


@router.get(
    "/exists",
    response_model=dict,
    responses=build_error_responses([]),
    summary="Check if agents exist for project",
)
async def check_agents_exist(
    project_id: uuid.UUID = Query(..., description="Project ID"),
    agent_service: AgentService = Depends(get_agent_service),
) -> dict:
    (
        '\n'
        '    Check if any agents exist for the specified project.\n'
        '\n'
        '    Returns a simple boolean response indicating whether the '
        'project has any agents.\n'
        '    This is useful for quick existence checks without fetching full '
        'agent data.\n'
        '    '
    )
    agents, total_count = await agent_service.list_agents(
        project_id=project_id,
        limit=1,
        offset=0,
    )
    return {"exists": total_count > 0, "count": total_count}


@router.delete(
    "/sessions/{session_id}/memory",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear session memory",
)
async def clear_session_memory(
    session_id: str,
    project_id: uuid.UUID = Query(...),
    user_id: Optional[str] = Query(
        default=None, description="Optional user ID to clear personal memories"
    ),
    agent_service: AgentService = Depends(get_agent_service),
) -> None:
    (
        'Clear all memory and session history for a specific session.\n'
        '    \n'
        '    This deletes records from agno memory and session tables in the '
        "'ai' schema.\n"
        '    If user_id is provided, it also clears personal memories for '
        'that user from agno_memories.\n'
        '    '
    )
    await agent_service.clear_session_memory(
        session_id=session_id, project_id=project_id, user_id=user_id
    )
    logger.info(
        (
            f'Cleared session memory for '
            f'{session_id}'
            f' (project: '
            f'{project_id}'
            f', user: '
            f'{user_id}'
            f')'
        )
    )


@router.get(
    "/workflow-release-audit",
    response_model=WorkflowReleaseAuditResponse,
)
async def get_workflow_release_audit(
    project_id: uuid.UUID = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> WorkflowReleaseAuditResponse:
    """List local employee bindings that still lack a fixed release."""
    return await list_unpinned_workflows(
        db,
        project_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{agent_id}/workflows/{workflow_id}/pin-legacy",
    response_model=WorkflowPinTransitionReceipt,
)
async def pin_legacy_agent_workflow(
    agent_id: uuid.UUID,
    workflow_id: str,
    command: WorkflowPinTransitionCommand,
    project_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    requested_by: uuid.UUID | None = Depends(optional_mutation_actor),
) -> WorkflowPinTransitionReceipt:
    if requested_by is None:
        raise HTTPException(403, "旧工作流切换需要管理员身份")
    return await WorkflowPinTransitionService(db).pin(
        project_id,
        requested_by,
        agent_id,
        workflow_id,
        command,
    )


@router.get(
    "/{agent_id}/workflow-pin-transitions",
    response_model=WorkflowPinTransitionHistory,
)
async def get_workflow_pin_transitions(
    agent_id: uuid.UUID,
    project_id: uuid.UUID = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    requested_by: uuid.UUID | None = Depends(optional_mutation_actor),
) -> WorkflowPinTransitionHistory:
    if requested_by is None:
        raise HTTPException(403, "旧工作流切换记录需要管理员身份")
    return await WorkflowPinTransitionService(db).history(
        project_id,
        requested_by,
        agent_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "",
    response_model=AgentListResponse,
    responses=build_error_responses([]),
)
async def list_agents(
    model: Optional[str] = Query(
        default=None,
        description=(
            'Filter by model (provider:model_name, e.g., openai:gpt-4)'
        ),
    ),
    is_default: Optional[bool] = Query(
        default=None, description="Filter by default agent status"
    ),
    bound_device_id: uuid.UUID
    | None = Query(default=None, description="Filter by bound device ID"),
    pagination: tuple[int, int] = Depends(get_pagination_params),
    project_id: uuid.UUID = Query(..., description="Project ID"),
    agent_service: AgentService = Depends(get_agent_service),
) -> AgentListResponse:
    (
        '\n'
        '    Retrieve all AI agents for the authenticated project with '
        'filtering and pagination.\n'
        '\n'
        '    Returns detailed agent information including associated tools '
        'and collections.\n'
        '\n'
        '    **Filtering Options:**\n'
        '    - Filter by LLM model (provider:model_name format)\n'
        '    - Filter by default agent status\n'
        '    - Pagination with configurable limits\n'
        '\n'
        '    **Response Details:**\n'
        '    - Each agent includes basic information (id, name, model, etc.)\n'
        '    - Associated tools with their configurations and permissions\n'
        '    - Associated collections with their metadata\n'
        '    **Project Scope:**\n'
        '    - Only agents belonging to the specified project_id are '
        'returned\n'
        '    '
    )
    limit, offset = pagination
    agents, total_count = await agent_service.list_agents(
        project_id=project_id,
        model=model,
        is_default=is_default,
        bound_device_id=bound_device_id,
        limit=limit,
        offset=offset,
    )

    return AgentListResponse(
        data=[AgentWithDetails.model_validate(agent) for agent in agents],
        pagination=PaginationMetadata(
            total=total_count,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total_count,
            has_prev=offset > 0,
        ),
    )


@router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    responses=build_error_responses(
        [400, 404, 409],
        {404: "Referenced collection not found", 409: "Agent name conflict"},
    ),
)
async def create_agent(
    agent_data: AgentCreate,
    project_id: uuid.UUID = Query(..., description="Project ID"),
    agent_service: AgentService = Depends(get_agent_service),
    requested_by: uuid.UUID | None = Depends(optional_mutation_actor),
) -> AgentResponse:
    """
    Create a new AI agent for the authenticated project.

    **Agent Configuration:**
    - Specify LLM model in "provider:model_name" format
    - Configure system instruction and behavior
    - Bind tools with specific permissions and configurations
    - Set default agent status (only one per project)

    **Tool Integration:**
    - Tools specified in "tool_provider:tool_name" format
    - Configure permissions and tool-specific settings
    - Enable/disable tools individually

    **Project Scope:**
    - Project is provided explicitly via the project_id query parameter
    - Cross-service consistency is maintained
    """
    agent = await agent_service.create_agent(
        project_id, agent_data, requested_by=requested_by
    )
    return AgentResponse.model_validate(agent)


@router.get(
    "/{agent_id}/device-action-readiness",
    response_model=AgentDeviceActionReadiness,
)
async def get_device_action_readiness(
    agent_id: uuid.UUID,
    project_id: uuid.UUID = Query(...),
    agent_service: AgentService = Depends(get_agent_service),
    principal: TrialPrincipal = Depends(require_trial_principal),
) -> AgentDeviceActionReadiness:
    if principal.project_id != project_id:
        raise HTTPException(403, "设备动作核验的企业身份不一致")
    return await agent_service.device_action_readiness(project_id, agent_id)


@router.get(
    "/{agent_id}",
    response_model=AgentWithDetails,
    responses=build_error_responses([404], {404: "Agent not found"}),
)
async def get_agent(
    agent_id: uuid.UUID,
    include_tools: bool = Query(
        default=True, description="Include tool bindings in the response"
    ),
    include_collections: bool = Query(
        default=False,
        description="Include collection bindings in the response",
    ),
    include_workflows: bool = Query(
        default=False, description="Include workflow bindings in the response"
    ),
    project_id: uuid.UUID = Query(..., description="Project ID"),
    agent_service: AgentService = Depends(get_agent_service),
) -> AgentWithDetails:
    """
    Retrieve detailed information about a specific AI agent including
    tool bindings and collection access if requested.
    """
    agent = await agent_service.get_agent(project_id, agent_id)
    return AgentWithDetails.model_validate(agent)


@router.patch(
    "/{agent_id}",
    response_model=AgentResponse,
    responses=build_error_responses(
        [400, 404, 409],
        {404: "Agent or collection not found", 409: "Agent name conflict"},
    ),
)
async def update_agent(
    agent_id: uuid.UUID,
    agent_data: AgentUpdate,
    project_id: uuid.UUID = Query(..., description="Project ID"),
    agent_service: AgentService = Depends(get_agent_service),
    requested_by: uuid.UUID | None = Depends(optional_mutation_actor),
) -> AgentResponse:
    """
    Update AI agent configuration, tools, or settings.

    **Update Capabilities:**
    - Modify agent name, instruction, and model
    - Reconfigure tool bindings
    - Change default agent status
    - Update agent-specific configuration
    """
    agent = await agent_service.update_agent(
        project_id, agent_id, agent_data, requested_by=requested_by
    )
    return AgentResponse.model_validate(agent)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=build_error_responses([404], {404: "Agent not found"}),
)
async def delete_agent(
    agent_id: uuid.UUID,
    project_id: uuid.UUID = Query(..., description="Project ID"),
    agent_service: AgentService = Depends(get_agent_service),
) -> None:
    """
    Soft delete an AI agent. The agent will be marked as deleted but
    preserved for audit purposes.
    """
    await agent_service.delete_agent(project_id, agent_id)


@router.patch(
    "/{agent_id}/tools/{tool_id}/enabled",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=build_error_responses(
        [400, 404], {404: "Agent or tool binding not found"}
    ),
)
async def set_agent_tool_enabled(
    agent_id: uuid.UUID,
    tool_id: uuid.UUID,
    payload: ToggleEnabledRequest,
    project_id: uuid.UUID = Query(..., description="Project ID"),
    agent_service: AgentService = Depends(get_agent_service),
) -> None:
    """Enable or disable a specific tool binding for an agent."""
    await agent_service.set_tool_enabled(
        project_id, agent_id, tool_id, payload.enabled
    )


@router.patch(
    "/{agent_id}/collections/{collection_id}/enabled",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=build_error_responses(
        [400, 404], {404: "Agent or collection binding not found"}
    ),
)
async def set_agent_collection_enabled(
    agent_id: uuid.UUID,
    collection_id: str,
    payload: ToggleEnabledRequest,
    project_id: uuid.UUID = Query(..., description="Project ID"),
    agent_service: AgentService = Depends(get_agent_service),
) -> None:
    """Enable or disable a specific collection binding for an agent."""
    await agent_service.set_collection_enabled(
        project_id, agent_id, collection_id, payload.enabled
    )


@router.patch(
    "/{agent_id}/workflows/{workflow_id}/enabled",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=build_error_responses(
        [400, 404], {404: "Agent or workflow binding not found"}
    ),
)
async def set_agent_workflow_enabled(
    agent_id: uuid.UUID,
    workflow_id: str,
    payload: ToggleEnabledRequest,
    project_id: uuid.UUID = Query(..., description="Project ID"),
    agent_service: AgentService = Depends(get_agent_service),
) -> None:
    """Enable or disable a specific workflow binding for an agent."""
    await agent_service.set_workflow_enabled(
        project_id, agent_id, workflow_id, payload.enabled
    )
