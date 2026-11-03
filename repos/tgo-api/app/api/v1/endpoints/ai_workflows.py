"""Workflow service proxy endpoints."""

import hmac
from typing import List, Literal, Optional, Union
from collections.abc import AsyncGenerator
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.common_responses import CREATE_RESPONSES, CRUD_RESPONSES, LIST_RESPONSES
from app.core.security import get_current_active_user, require_admin
from app.core.database import get_db
from app.models import Staff
from app.schemas.ai_workflows import (
    PaginatedWorkflowSummaryResponse,
    WorkflowCreate,
    WorkflowCustomerMessageLookup,
    WorkflowChannelMessageLookup,
    WorkflowDuplicateRequest,
    WorkflowExecuteRequest,
    WorkflowExecution,
    WorkflowExecutionCancelResponse,
    WorkflowInDB,
    WorkflowSyncResponse,
    WorkflowUpdate,
    WorkflowValidationResponse,
    WorkflowValidateRequest,
    WorkflowVariablesResponse,
)
from app.services.workflow_client import workflow_client
from app.services.aftersales_review_files import (
    list_review_files, require_customer_read, require_intake_evidence_files,
    review_chat_target,
)
from app.services.aftersales_review_handoff import (
    get_aftersales_handoff, request_aftersales_handoff,
)
from app.services.workflow_approval_assignee import require_approval_assignees
from app.services.workflow_trace import (
    find_business_request_for_message,
    find_ai_request_for_channel_message,
    page_workflow_records_for_ai_request,
    page_workflow_records_for_ai_request_after,
)
from app.services.workflow_stream import prepare_workflow_stream
from app.schemas.workflow_capabilities import WorkflowCapabilities
from app.schemas.workflow_device_references import WorkflowDeviceReferences
from app.schemas.business_query_audit import BusinessQueryAuditList
from app.services.business_query_audit_client import read_business_query_audits
from app.schemas.workflow_releases import WorkflowPublishRequest, WorkflowReleaseSummary, WorkflowRollbackRequest
from app.schemas.workflow_approval import (
    WorkflowApprovalDecision, WorkflowApprovalDecisionResult,
    WorkflowApprovalSummary, WorkflowAfterSalesReviewSummary,
    WorkflowAfterSalesReviewDecision,
    WorkflowAfterSalesEvidenceFile,
    WorkflowAfterSalesChatTarget,
    WorkflowAfterSalesIntakeCreate, WorkflowAfterSalesIntakeSummary,
    WorkflowAfterSalesHandoffSummary,
)

router = APIRouter()


@router.get("/aftersales-reviews", response_model=list[WorkflowAfterSalesReviewSummary])
async def list_my_aftersales_reviews(
    response: Response,
    limit: int = Query(100, ge=1, le=100),
    before_status: Literal["pending_staff_review", "reviewed"] | None = Query(None),
    before_created_at: datetime | None = Query(None),
    before_id: UUID | None = Query(None),
    current_user: Staff = Depends(get_current_active_user),
) -> list[WorkflowAfterSalesReviewSummary]:
    response.headers["Cache-Control"] = "no-store"
    if any(value is not None for value in (before_status, before_created_at, before_id)) and not all(
        value is not None for value in (before_status, before_created_at, before_id)
    ):
        raise HTTPException(400, "售后待办分页位置不完整")
    return await workflow_client.list_aftersales_reviews(
        str(current_user.project_id), str(current_user.id),
        limit=limit, before_status=before_status,
        before_created_at=before_created_at.isoformat() if before_created_at else None,
        before_id=str(before_id) if before_id else None,
    )


@router.get(
    "/aftersales-reviews/{review_id}/files",
    response_model=list[WorkflowAfterSalesEvidenceFile],
)
async def list_my_aftersales_review_files(
    review_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> list[WorkflowAfterSalesEvidenceFile]:
    response.headers["Cache-Control"] = "no-store"
    review = await workflow_client.get_aftersales_review(
        str(current_user.project_id), str(current_user.id), str(review_id),
    )
    return await list_review_files(db, current_user, review.visitor_session_id)


@router.get(
    "/aftersales-reviews/{review_id}/chat-target",
    response_model=WorkflowAfterSalesChatTarget,
    response_model_exclude_none=True,
)
async def get_my_aftersales_chat_target(
    review_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowAfterSalesChatTarget:
    response.headers["Cache-Control"] = "no-store"
    review = await workflow_client.get_aftersales_review(
        str(current_user.project_id), str(current_user.id), str(review_id),
    )
    return await review_chat_target(
        db, current_user, review.visitor_session_id,
        execution_id=review.execution_id,
    )


@router.get(
    "/aftersales-reviews/{review_id}/intake",
    response_model=WorkflowAfterSalesIntakeSummary | None,
)
async def get_my_aftersales_intake(
    review_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowAfterSalesIntakeSummary | None:
    response.headers["Cache-Control"] = "no-store"
    require_customer_read(db, current_user)
    return await workflow_client.get_aftersales_intake(
        str(current_user.project_id), str(current_user.id), str(review_id),
    )


@router.post(
    "/aftersales-reviews/{review_id}/intake",
    response_model=WorkflowAfterSalesIntakeSummary,
)
async def prepare_my_aftersales_intake(
    review_id: UUID,
    request: WorkflowAfterSalesIntakeCreate,
    response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowAfterSalesIntakeSummary:
    response.headers["Cache-Control"] = "no-store"
    require_customer_read(db, current_user)
    review = await workflow_client.get_aftersales_review(
        str(current_user.project_id), str(current_user.id), str(review_id),
    )
    if (
        review.status != "reviewed" or review.review_result != "needs_manual_service"
        or review.reviewed_by != current_user.id
        or not hmac.compare_digest(review.review_digest, request.review_digest)
    ):
        raise HTTPException(409, "售后核对内容已变化，请刷新后重新填写。")
    existing = await workflow_client.get_aftersales_intake(
        str(current_user.project_id), str(current_user.id), str(review_id),
    )
    if existing is not None:
        if (
            existing.order_reference == request.order_reference
            and existing.sku == request.sku
            and existing.request_reason == request.request_reason
            and existing.policy_reference == request.policy_reference
            and sorted(existing.evidence_file_ids) == sorted(request.evidence_file_ids)
        ):
            return existing
        raise HTTPException(409, "内部售后登记已保存其他内容，不能覆盖。")
    await require_intake_evidence_files(
        db, current_user, review.visitor_session_id, request.evidence_file_ids,
    )
    return await workflow_client.prepare_aftersales_intake(
        str(current_user.project_id), str(current_user.id), str(review_id), request,
    )


@router.get(
    "/aftersales-reviews/{review_id}/handoff",
    response_model=WorkflowAfterSalesHandoffSummary | None,
)
async def get_my_aftersales_handoff(
    review_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowAfterSalesHandoffSummary | None:
    response.headers["Cache-Control"] = "no-store"
    return await get_aftersales_handoff(db, current_user, review_id)


@router.post(
    "/aftersales-reviews/{review_id}/handoff",
    response_model=WorkflowAfterSalesHandoffSummary,
)
async def request_my_aftersales_handoff(
    review_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowAfterSalesHandoffSummary:
    response.headers["Cache-Control"] = "no-store"
    return await request_aftersales_handoff(db, current_user, review_id)


@router.post(
    "/aftersales-reviews/{review_id}/decision",
    response_model=WorkflowAfterSalesReviewSummary,
)
async def decide_my_aftersales_review(
    review_id: UUID,
    decision: WorkflowAfterSalesReviewDecision,
    response: Response,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowAfterSalesReviewSummary:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_client.decide_aftersales_review(
        str(current_user.project_id), str(current_user.id),
        str(review_id), decision,
    )


@router.get("/approvals", response_model=list[WorkflowApprovalSummary])
async def list_my_workflow_approvals(
    current_user: Staff = Depends(get_current_active_user),
) -> list[WorkflowApprovalSummary]:
    return await workflow_client.list_approvals(
        str(current_user.project_id), str(current_user.id),
    )


@router.get(
    "/approvals/{approval_id}/chat-target",
    response_model=WorkflowAfterSalesChatTarget,
    response_model_exclude_none=True,
)
async def get_my_approval_chat_target(
    approval_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowAfterSalesChatTarget:
    response.headers["Cache-Control"] = "no-store"
    origin = await workflow_client.get_approval_customer_session(
        str(current_user.project_id), str(current_user.id), str(approval_id),
    )
    return await review_chat_target(
        db, current_user, origin.visitor_session_id,
        source_message_id=origin.source_message_id,
        source_channel=origin.source_channel,
    )


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=WorkflowApprovalDecisionResult,
)
async def decide_my_workflow_approval(
    approval_id: UUID,
    request: WorkflowApprovalDecision,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowApprovalDecisionResult:
    return await workflow_client.decide_approval(
        str(current_user.project_id), str(current_user.id),
        str(approval_id), request.decision, request.preview_digest,
    )


@router.get("/capabilities", response_model=WorkflowCapabilities)
async def get_workflow_capabilities(
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowCapabilities:
    return await workflow_client.get_capabilities(str(current_user.project_id))


@router.get("/device-references", response_model=WorkflowDeviceReferences)
async def list_device_workflow_references(
    device_id: UUID = Query(...),
    limit: int = Query(5, ge=1, le=20),
    offset: int = Query(0, ge=0),
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowDeviceReferences:
    return await workflow_client.list_device_references(
        str(current_user.project_id), str(device_id), limit=limit, offset=offset,
    )


@router.get(
    "/business-queries/{query_id}/audits",
    response_model=BusinessQueryAuditList,
)
async def get_workflow_business_query_audits(
    query_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
) -> BusinessQueryAuditList:
    response.headers["Cache-Control"] = "no-store"
    return await read_business_query_audits(current_user.project_id, query_id)


@router.get(
    "",
    response_model=PaginatedWorkflowSummaryResponse,
    responses=LIST_RESPONSES,
    summary="List Workflows",
)
async def list_workflows(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    sort_by: str = Query("updated_at"),
    sort_order: str = Query("desc"),
    current_user: Staff = Depends(get_current_active_user),
) -> PaginatedWorkflowSummaryResponse:
    """List workflows from Workflow service."""
    data = await workflow_client.list_workflows(
        project_id=str(current_user.project_id),
        skip=skip,
        limit=limit,
        status=status,
        search=search,
        tags=tags,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return PaginatedWorkflowSummaryResponse.model_validate(data)


@router.post(
    "",
    response_model=WorkflowInDB,
    responses=CREATE_RESPONSES,
    status_code=201,
    summary="Create Workflow",
)
async def create_workflow(
    workflow_data: WorkflowCreate,
    current_user: Staff = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> WorkflowInDB:
    """Create workflow in Workflow service."""
    require_approval_assignees(workflow_data.nodes, db, current_user.project_id)
    data = await workflow_client.create_workflow(
        str(current_user.project_id), workflow_data.model_dump(by_alias=True)
    )
    return WorkflowInDB.model_validate(data)


@router.get(
    "/{workflow_id}",
    response_model=WorkflowInDB,
    responses=CRUD_RESPONSES,
    summary="Get Workflow",
)
async def get_workflow(
    workflow_id: str,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowInDB:
    """Get workflow from Workflow service."""
    data = await workflow_client.get_workflow(workflow_id, str(current_user.project_id))
    return WorkflowInDB.model_validate(data)


@router.put(
    "/{workflow_id}",
    response_model=WorkflowInDB,
    responses=CRUD_RESPONSES,
    summary="Update Workflow",
)
async def update_workflow(
    workflow_id: str,
    workflow_data: WorkflowUpdate,
    current_user: Staff = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> WorkflowInDB:
    """Update workflow in Workflow service."""
    if workflow_data.nodes is not None:
        require_approval_assignees(workflow_data.nodes, db, current_user.project_id)
    data = await workflow_client.update_workflow(
        workflow_id,
        str(current_user.project_id),
        workflow_data.model_dump(by_alias=True, exclude_none=True),
    )
    return WorkflowInDB.model_validate(data)


@router.delete(
    "/{workflow_id}",
    responses=CRUD_RESPONSES,
    status_code=204,
    summary="Delete Workflow",
)
async def delete_workflow(
    workflow_id: str,
    current_user: Staff = Depends(get_current_active_user),
) -> None:
    """Delete workflow from Workflow service."""
    await workflow_client.delete_workflow(workflow_id, str(current_user.project_id))


@router.post(
    "/{workflow_id}/duplicate",
    response_model=WorkflowInDB,
    responses=CRUD_RESPONSES,
    summary="Duplicate Workflow",
)
async def duplicate_workflow(
    workflow_id: str,
    request: Optional[WorkflowDuplicateRequest] = None,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowInDB:
    """Duplicate workflow in Workflow service."""
    payload = request.model_dump(by_alias=True, exclude_none=True) if request else None
    data = await workflow_client.duplicate_workflow(
        workflow_id, str(current_user.project_id), payload
    )
    return WorkflowInDB.model_validate(data)


@router.post(
    "/validate",
    response_model=WorkflowValidationResponse,
    responses=CRUD_RESPONSES,
    summary="Validate Workflow Generic",
)
async def validate_workflow_generic(
    request: WorkflowValidateRequest,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowValidationResponse:
    """Validate an arbitrary workflow graph (nodes/edges) in Workflow service."""
    data = await workflow_client.validate_workflow_generic(
        str(current_user.project_id), request.model_dump(by_alias=True)
    )
    return WorkflowValidationResponse.model_validate(data)


@router.post(
    "/{workflow_id}/validate",
    response_model=WorkflowValidationResponse,
    responses=CRUD_RESPONSES,
    summary="Validate Workflow",
)
async def validate_workflow(
    workflow_id: str,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowValidationResponse:
    """Validate an existing workflow by id in Workflow service."""
    data = await workflow_client.validate_workflow(workflow_id, str(current_user.project_id))
    return WorkflowValidationResponse.model_validate(data)


@router.post(
    "/{workflow_id}/publish",
    response_model=WorkflowInDB,
    responses=CRUD_RESPONSES,
    summary="Publish Workflow",
)
async def publish_workflow(
    workflow_id: str,
    request: WorkflowPublishRequest,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WorkflowInDB:
    """Publish a workflow in Workflow service."""
    draft = WorkflowInDB.model_validate(await workflow_client.get_workflow(
        workflow_id, str(current_user.project_id),
    ))
    if (draft.version == request.expected_draft_version
            and draft.publication_revision == request.expected_publication_revision):
        require_approval_assignees(draft.definition.nodes, db, current_user.project_id)
    data = await workflow_client.publish_workflow(workflow_id, str(current_user.project_id), request, str(current_user.id))
    return WorkflowInDB.model_validate(data)


@router.get("/{workflow_id}/releases", response_model=list[WorkflowReleaseSummary])
async def get_workflow_releases(workflow_id: str, current_user: Staff = Depends(get_current_active_user)) -> list[WorkflowReleaseSummary]:
    rows = await workflow_client.workflow_releases(workflow_id, str(current_user.project_id))
    return [WorkflowReleaseSummary.model_validate(row) for row in rows]


@router.post("/{workflow_id}/rollback", response_model=WorkflowInDB)
async def rollback_workflow(workflow_id: str, request: WorkflowRollbackRequest, current_user: Staff = Depends(require_admin())) -> WorkflowInDB:
    data = await workflow_client.rollback_workflow(workflow_id, str(current_user.project_id), request)
    return WorkflowInDB.model_validate(data)


@router.get(
    "/{workflow_id}/variables",
    response_model=WorkflowVariablesResponse,
    responses=CRUD_RESPONSES,
    summary="Get Workflow Variables",
)
async def get_workflow_variables(
    workflow_id: str,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowVariablesResponse:
    """Get available variables for a workflow in Workflow service."""
    data = await workflow_client.get_workflow_variables(
        workflow_id, str(current_user.project_id)
    )
    return WorkflowVariablesResponse.model_validate(data)


@router.post(
    "/{workflow_id}/execute",
    summary="Execute Workflow",
    response_model=None,
    description="""
Execute a workflow using one of three supported modes:

1. **Synchronous Mode (Default, `stream=False`, `async=False`)**:
   Executes the workflow immediately and returns the final output. Ideal for short-lived tasks.

2. **Asynchronous Mode (`async=True`)**:
   Creates an execution record and triggers a background Celery task. Returns the execution record immediately.

3. **Streaming Mode (`stream=True`)**:
   Returns a Server-Sent Events (SSE) stream, pushing real-time events as the workflow progresses.

### Streaming Mode Events (SSE):
When `stream=True`, the response header includes `Content-Type: text/event-stream`.
Each message starts with `data: ` followed by a JSON string and ends with `\\n\\n`.

- **workflow_started**: Workflow execution initialized.
- **node_started**: A specific node has started executing.
- **node_finished**: A specific node has finished (success or failure).
- **workflow_finished**: Entire workflow has finished.
""",
    responses={
        200: {
            "description": "Successful Response",
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            {"$ref": "#/components/schemas/WorkflowSyncResponse"},
                            {"$ref": "#/components/schemas/WorkflowExecution"},
                        ]
                    },
                    "examples": {
                        "sync_mode": {
                            "summary": "Synchronous Mode (Default)",
                            "description": "Returns final output and metadata.",
                            "value": {
                                "success": True,
                                "output": {"answer": "Paris is the capital of France."},
                                "metadata": {
                                    "duration": 1.234,
                                    "startTime": "2026-01-03T06:16:44.478Z",
                                    "endTime": "2026-01-03T06:16:45.712Z",
                                },
                            },
                        },
                        "async_mode": {
                            "summary": "Asynchronous Mode (async=true)",
                            "description": "Returns the execution record with 'pending' status.",
                            "value": {
                                 "id": "d73d5f18-3cc0-400f-ab18-c0a5f05625f5",
                                "project_id": "proj-123",
                                "workflow_id": "wf-456",
                                "status": "pending",
                                "input": {"query": "What is the capital of France?"},
                                "output": None,
                                "error": None,
                                "started_at": "2026-01-03T06:16:44.478Z",
                                "completed_at": None,
                                "duration": None,
                                "node_executions": []
                            },
                        },
                    },
                },
                "text/event-stream": {
                    "description": "Streaming Mode (stream=true)",
                    "example": (
                        "data: {\"event\": \"workflow_started\", \"workflow_run_id\": \"uuid-1\", \"task_id\": \"stream-uuid-1\", \"data\": {\"id\": \"uuid-1\", \"workflow_id\": \"wf-1\", \"inputs\": {}, \"created_at\": 1735790000}}\n\n"
                        "data: {\"event\": \"node_started\", \"workflow_run_id\": \"uuid-1\", \"task_id\": \"stream-uuid-1\", \"data\": {\"id\": \"node-exec-1\", \"node_id\": \"node-1\", \"node_type\": \"llm\", \"title\": \"AI Chat\", \"index\": 1, \"created_at\": 1735790001}}\n\n"
                        "data: {\"event\": \"node_finished\", \"workflow_run_id\": \"uuid-1\", \"task_id\": \"stream-uuid-1\", \"data\": {\"id\": \"node-exec-1\", \"node_id\": \"node-1\", \"node_type\": \"llm\", \"inputs\": {}, \"outputs\": {\"text\": \"Hello\"}, \"status\": \"succeeded\", \"error\": null, \"elapsed_time\": 0.5, \"finished_at\": 1735790002}}\n\n"
                        "data: {\"event\": \"workflow_finished\", \"workflow_run_id\": \"uuid-1\", \"task_id\": \"stream-uuid-1\", \"data\": {\"id\": \"uuid-1\", \"workflow_id\": \"wf-1\", \"status\": \"succeeded\", \"outputs\": {\"result\": \"Hello\"}, \"error\": null, \"elapsed_time\": 0.6, \"total_steps\": 1, \"finished_at\": 1735790002}}\n\n"
                    ),
                },
            },
        },
        404: {"description": "Workflow not found"},
    },
)
async def execute_workflow(
    workflow_id: str,
    request: WorkflowExecuteRequest,
    current_user: Staff = Depends(get_current_active_user),
) -> Union[WorkflowSyncResponse, WorkflowExecution, StreamingResponse]:
    """Execute workflow in Workflow service."""
    res = await workflow_client.execute_workflow(
        workflow_id, str(current_user.project_id), request.model_dump(by_alias=True)
    )

    if request.stream:
        if not isinstance(res, AsyncGenerator):
            raise HTTPException(502, "工作流服务未返回有效数据流，请重试。")
        ready_stream = await prepare_workflow_stream(res)
        return StreamingResponse(ready_stream, media_type="text/event-stream")

    # Determine if it's sync or async response
    if request.async_mode:
        return WorkflowExecution.model_validate(res)
    else:
        return WorkflowSyncResponse.model_validate(res)


@router.get(
    "/executions/{execution_id}",
    response_model=WorkflowExecution,
    summary="Get Execution Status",
)
async def get_execution(
    execution_id: str,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowExecution:
    """Get execution status from Workflow service."""
    data = await workflow_client.get_execution(execution_id, str(current_user.project_id))
    return WorkflowExecution.model_validate(data)


@router.get(
    "/{workflow_id}/executions",
    response_model=List[WorkflowExecution],
    summary="List Workflow Executions",
)
async def list_workflow_executions(
    workflow_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Staff = Depends(get_current_active_user),
    before_started_at: str | None = None,
    before_id: str | None = None,
) -> List[WorkflowExecution]:
    """List workflow executions from Workflow service."""
    data = await workflow_client.list_workflow_executions(
        workflow_id, str(current_user.project_id), skip=skip, limit=limit,
        before_started_at=before_started_at, before_id=before_id,
    )
    return [WorkflowExecution.model_validate(item) for item in data]


@router.get(
    "/executions/by-business-request/{business_request_id}",
    response_model=List[WorkflowExecution],
    summary="List Workflow Executions for a Customer Request",
)
async def list_business_request_executions(
    business_request_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: Staff = Depends(require_admin()),
    before_started_at: str | None = None,
    before_id: str | None = None,
) -> List[WorkflowExecution]:
    return await workflow_client.list_business_request_executions(
        str(business_request_id), str(current_user.project_id), limit=limit,
        skip=skip, before_started_at=before_started_at, before_id=before_id,
    )


@router.get(
    "/executions/by-parent/{parent_execution_id}",
    response_model=List[WorkflowExecution],
    summary="List Child Workflow Executions",
)
async def list_child_executions(
    parent_execution_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: Staff = Depends(require_admin()),
    before_started_at: str | None = None,
    before_id: str | None = None,
) -> List[WorkflowExecution]:
    return await workflow_client.list_child_executions(
        str(parent_execution_id), str(current_user.project_id),
        limit=limit, skip=skip,
        before_started_at=before_started_at, before_id=before_id,
    )


@router.get(
    "/executions/by-visitor-session/{visitor_session_id}",
    response_model=List[WorkflowExecution],
    summary="List Workflow Executions for a Customer Session",
)
async def list_visitor_session_executions(
    visitor_session_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: Staff = Depends(require_admin()),
    before_started_at: str | None = None,
    before_id: str | None = None,
) -> List[WorkflowExecution]:
    return await workflow_client.list_visitor_session_executions(
        str(visitor_session_id), str(current_user.project_id), limit=limit,
        skip=skip, before_started_at=before_started_at, before_id=before_id,
    )


@router.post(
    "/executions/lookup-customer-message",
    response_model=List[WorkflowExecution],
    summary="Find Workflow Executions for an Original Customer Message",
)
async def lookup_customer_message_executions(
    request: WorkflowCustomerMessageLookup,
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
    before_started_at: str | None = None,
    before_id: str | None = None,
) -> List[WorkflowExecution]:
    request_id = find_business_request_for_message(
        db, current_user.project_id, request.visitor_session_id,
        request.source_message_id,
    )
    if request_id is None:
        return []
    records = await workflow_client.list_business_request_executions(
        str(request_id), str(current_user.project_id), limit=limit,
        visitor_session_id=str(request.visitor_session_id),
        skip=skip, before_started_at=before_started_at, before_id=before_id,
    )
    return [record for record in records
            if record.business_request_id == str(request_id)
            and record.visitor_session_id == str(request.visitor_session_id)]


@router.post(
    "/executions/lookup-channel-message",
    response_model=List[WorkflowExecution],
    summary="Find Workflow Executions from a Customer Chat Message",
)
async def lookup_channel_message_executions(
    request: WorkflowChannelMessageLookup,
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
    before_started_at: str | None = None,
    before_id: str | None = None,
) -> List[WorkflowExecution]:
    if (before_started_at is None) != (before_id is None):
        raise HTTPException(400, "before_started_at and before_id must be provided together")
    run = find_ai_request_for_channel_message(
        db, current_user.project_id, request.channel_id, request.source_message_id,
    )
    if run is None:
        return []
    if before_started_at is not None:
        async def fetch_after(
            anchor_at: str | None, anchor_id: str | None, page_limit: int,
        ) -> list[WorkflowExecution]:
            return await workflow_client.list_business_request_executions(
                str(run.id), str(current_user.project_id), limit=page_limit,
                before_started_at=anchor_at, before_id=anchor_id,
            )

        return await page_workflow_records_for_ai_request_after(
            db, run, limit, before_started_at, before_id, fetch_after,
        )
    async def fetch_page(raw_skip: int, page_limit: int) -> list[WorkflowExecution]:
        return await workflow_client.list_business_request_executions(
            str(run.id), str(current_user.project_id), limit=page_limit,
            skip=raw_skip,
        )

    return await page_workflow_records_for_ai_request(db, run, limit, skip, fetch_page)


@router.post(
    "/executions/{execution_id}/cancel",
    response_model=WorkflowExecutionCancelResponse,
    responses=CRUD_RESPONSES,
    summary="Cancel Execution",
)
async def cancel_execution(
    execution_id: str,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowExecutionCancelResponse:
    """Cancel a workflow execution in Workflow service."""
    data = await workflow_client.cancel_execution(execution_id, str(current_user.project_id))
    return WorkflowExecutionCancelResponse.model_validate(data)

