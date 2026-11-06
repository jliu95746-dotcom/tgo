"""Proxy requests to the configured internal Workflow service."""

from collections.abc import AsyncGenerator, Mapping
from typing import List, Literal, Optional
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.schemas.ai_workflows import WorkflowExecution, WorkflowInDB
from app.schemas.webhook_delivery import WebhookAccepted
from app.schemas.webhook_history import WebhookDeliveryList
from app.schemas.workflow_ai_usage import (
    WorkflowAICacheCommand,
    WorkflowAICacheReceipt,
    WorkflowAICacheWrite,
    WorkflowAICommand,
    WorkflowAIContext,
    WorkflowUsageOutcome,
)
from app.schemas.workflow_approval import (
    WorkflowAfterSalesIntakeCreate,
    WorkflowAfterSalesIntakeSummary,
    WorkflowAfterSalesReviewDecision,
    WorkflowAfterSalesReviewSummary,
    WorkflowApprovalCustomerSession,
    WorkflowApprovalDecisionResult,
    WorkflowApprovalSummary,
)
from app.schemas.workflow_capabilities import WorkflowCapabilities
from app.schemas.workflow_device_references import WorkflowDeviceReferences
from app.schemas.workflow_event_subscriptions import WorkflowSourceEventRequest
from app.schemas.workflow_events import (
    WorkflowEventAccepted,
    WorkflowEventTestContext,
    WorkflowEventTestRequest,
)
from app.schemas.workflow_reconciliation import (
    WorkflowReconciliationDecision,
    WorkflowReconciliationDecisionResult,
    WorkflowReconciliationSummary,
)
from app.schemas.workflow_releases import (
    WorkflowPublishRequest,
    WorkflowReleaseSummary,
    WorkflowRollbackRequest,
)
from app.schemas.workflow_schedule import (
    ScheduleDisable,
    ScheduleReceipt,
    ScheduleSave,
    ScheduleState,
)
from app.schemas.workflow_schedule_views import (
    ScheduleOccurrenceList,
    SchedulePreview,
    SchedulePreviewRequest,
)
from app.schemas.workflow_webhook import (
    WebhookChange,
    WebhookReceipt,
    WebhookSave,
    WebhookState,
)
from app.services.webhook_http import SIGNATURE_HEADERS
from app.services.workflow_event_signing import (
    sign_event_test,
    sign_source_event,
)
from app.services.workflow_reconciliation_contracts import (
    reconciliation_contract,
    reconciliation_list,
)
from app.services.workflow_transport import (
    JsonObject,
    RequestParams,
    WorkflowTransport,
)


class WorkflowServiceClient(WorkflowTransport):
    """Client for communicating with the internal Workflow service."""

    def __init__(self) -> None:
        self.base_url = settings.WORKFLOW_SERVICE_URL.rstrip("/")
        self.timeout = settings.WORKFLOW_SERVICE_TIMEOUT
        self.api_key = settings.WORKFLOW_SERVICE_API_KEY

    def _schedule_headers(self) -> dict[str, str]:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "定时计划内部身份核验未配置。")
        return {"X-Internal-API-Key": settings.SECRET_KEY}

    def _webhook_headers(self) -> dict[str, str]:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "外部触发内部身份核验未配置。")
        return {"X-Internal-API-Key": settings.SECRET_KEY}

    async def accept_webhook(
        self,
        hook_id: UUID,
        body: bytes,
        headers: dict[str, str],
        content_type: str,
    ) -> WebhookAccepted:
        transport = {
            wire_name: headers[name]
            for name, wire_name in SIGNATURE_HEADERS.items()
        }
        transport.update(self._webhook_headers())
        transport["Content-Type"] = content_type
        response = await self._make_request(
            "POST",
            "/v1/webhook-deliveries/" + str(hook_id),
            raw_body=body,
            extra_headers=transport,
        )
        return WebhookAccepted.model_validate(
            await self._handle_response(response)
        )

    @staticmethod
    def _webhook_path(workflow_id: str) -> str:
        return f"/v1/workflows/{quote(workflow_id, safe='')}/webhook"

    async def get_webhook(
        self,
        workflow_id: str,
        project_id: UUID,
    ) -> WebhookState:
        response = await self._make_request(
            "GET",
            self._webhook_path(workflow_id),
            params={"project_id": str(project_id)},
            extra_headers=self._webhook_headers(),
        )
        return WebhookState.model_validate(
            await self._handle_response(response)
        )

    async def get_webhook_deliveries(
        self,
        workflow_id: str,
        project_id: UUID,
        limit: int = 10,
        offset: int = 0,
    ) -> WebhookDeliveryList:
        response = await self._make_request(
            "GET",
            self._webhook_path(workflow_id) + "/deliveries",
            params={
                "project_id": str(project_id),
                "limit": limit,
                "offset": offset,
            },
            extra_headers=self._webhook_headers(),
        )
        return WebhookDeliveryList.model_validate(
            await self._handle_response(response)
        )

    async def save_webhook(
        self,
        workflow_id: str,
        project_id: UUID,
        actor_id: UUID,
        request: WebhookSave,
    ) -> WebhookReceipt:
        payload = request.model_dump(mode="json")
        payload.update(
            project_id=str(project_id), actor_staff_id=str(actor_id)
        )
        response = await self._make_request(
            "PUT",
            self._webhook_path(workflow_id),
            json_data=payload,
            extra_headers=self._webhook_headers(),
        )
        return WebhookReceipt.model_validate(
            await self._handle_response(response)
        )

    async def change_webhook(
        self,
        workflow_id: str,
        project_id: UUID,
        actor_id: UUID,
        operation: Literal["disable", "rotate"],
        request: WebhookChange,
    ) -> WebhookReceipt:
        payload = request.model_dump(mode="json")
        payload.update(
            project_id=str(project_id), actor_staff_id=str(actor_id)
        )
        response = await self._make_request(
            "POST",
            self._webhook_path(workflow_id) + "/" + operation,
            json_data=payload,
            extra_headers=self._webhook_headers(),
        )
        return WebhookReceipt.model_validate(
            await self._handle_response(response)
        )

    async def get_webhook_command(
        self,
        workflow_id: str,
        project_id: UUID,
        actor_id: UUID,
        request_id: UUID,
    ) -> WebhookReceipt:
        response = await self._make_request(
            "GET",
            self._webhook_path(workflow_id) + "/commands/" + str(request_id),
            params={
                "project_id": str(project_id),
                "actor_staff_id": str(actor_id),
            },
            extra_headers=self._webhook_headers(),
        )
        return WebhookReceipt.model_validate(
            await self._handle_response(response)
        )

    async def preview_schedule(
        self,
        workflow_id: str,
        project_id: UUID,
        request: SchedulePreviewRequest,
    ) -> SchedulePreview:
        response = await self._make_request(
            "POST",
            f"/v1/workflows/{workflow_id}/schedule/preview",
            params={"project_id": str(project_id)},
            json_data=request.model_dump(mode="json"),
            extra_headers=self._schedule_headers(),
        )
        return SchedulePreview.model_validate(
            await self._handle_response(response)
        )

    async def list_schedule_occurrences(
        self,
        workflow_id: str,
        project_id: UUID,
        limit: int,
        offset: int,
    ) -> ScheduleOccurrenceList:
        response = await self._make_request(
            "GET",
            f"/v1/workflows/{workflow_id}/schedule/occurrences",
            params={
                "project_id": str(project_id),
                "limit": limit,
                "offset": offset,
            },
            extra_headers=self._schedule_headers(),
        )
        return ScheduleOccurrenceList.model_validate(
            await self._handle_response(response)
        )

    async def get_schedule(
        self,
        workflow_id: str,
        project_id: UUID,
    ) -> ScheduleState:
        response = await self._make_request(
            "GET",
            f"/v1/workflows/{workflow_id}/schedule",
            params={"project_id": str(project_id)},
            extra_headers=self._schedule_headers(),
        )
        return ScheduleState.model_validate(
            await self._handle_response(response)
        )

    async def save_schedule(
        self,
        workflow_id: str,
        project_id: UUID,
        actor_id: UUID,
        request: ScheduleSave,
    ) -> ScheduleReceipt:
        payload = request.model_dump(mode="json")
        payload.update(
            project_id=str(project_id), actor_staff_id=str(actor_id)
        )
        response = await self._make_request(
            "PUT",
            f"/v1/workflows/{workflow_id}/schedule",
            json_data=payload,
            extra_headers=self._schedule_headers(),
        )
        return ScheduleReceipt.model_validate(
            await self._handle_response(response)
        )

    async def disable_schedule(
        self,
        workflow_id: str,
        project_id: UUID,
        actor_id: UUID,
        request: ScheduleDisable,
    ) -> ScheduleReceipt:
        payload = request.model_dump(mode="json")
        payload.update(
            project_id=str(project_id), actor_staff_id=str(actor_id)
        )
        response = await self._make_request(
            "POST",
            f"/v1/workflows/{workflow_id}/schedule/disable",
            json_data=payload,
            extra_headers=self._schedule_headers(),
        )
        return ScheduleReceipt.model_validate(
            await self._handle_response(response)
        )

    async def get_schedule_command(
        self,
        workflow_id: str,
        project_id: UUID,
        actor_id: UUID,
        request_id: UUID,
    ) -> ScheduleReceipt:
        response = await self._make_request(
            "GET",
            f"/v1/workflows/{workflow_id}/schedule/commands/{request_id}",
            params={
                "project_id": str(project_id),
                "actor_staff_id": str(actor_id),
            },
            extra_headers=self._schedule_headers(),
        )
        return ScheduleReceipt.model_validate(
            await self._handle_response(response)
        )

    async def get_event_test_context(
        self,
        workflow_id: str,
        project_id: str,
    ) -> WorkflowEventTestContext:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部事件核验未配置。")
        response = await self._make_request(
            "GET",
            f"/v1/workflows/{workflow_id}/event-test-context",
            params={"project_id": project_id},
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        data = await self._handle_response(response)
        return WorkflowEventTestContext.model_validate(data)

    async def send_event_test(
        self,
        workflow_id: str,
        project_id: str,
        actor_staff_id: str,
        request: WorkflowEventTestRequest,
    ) -> WorkflowEventAccepted:
        signed = sign_event_test(
            workflow_id,
            project_id,
            actor_staff_id,
            request,
            settings.SECRET_KEY,
        )
        response = await self._make_request(
            "POST",
            f"/v1/workflows/{workflow_id}/events",
            raw_body=signed.body,
            extra_headers=signed.headers,
        )
        data = await self._handle_response(response)
        return WorkflowEventAccepted.model_validate(data)

    async def get_capabilities(self, project_id: str) -> WorkflowCapabilities:
        response = await self._make_request(
            "GET",
            "/v1/workflows/capabilities",
            params={"project_id": project_id},
        )
        data = await self._handle_response(response)
        return WorkflowCapabilities.model_validate(data)

    async def send_source_event(
        self,
        workflow_id: str,
        project_id: str,
        delivery_id: UUID,
        request: WorkflowSourceEventRequest,
    ) -> WorkflowEventAccepted:
        signed = sign_source_event(
            workflow_id,
            project_id,
            delivery_id,
            request,
            settings.SECRET_KEY,
        )
        response = await self._make_request(
            "POST",
            f"/v1/workflows/{workflow_id}/events",
            raw_body=signed.body,
            extra_headers=signed.headers,
        )
        return WorkflowEventAccepted.model_validate(
            await self._handle_response(response)
        )

    async def list_device_references(
        self,
        project_id: str,
        device_id: str,
        *,
        limit: int = 5,
        offset: int = 0,
    ) -> WorkflowDeviceReferences:
        response = await self._make_request(
            "GET",
            "/v1/workflows/device-references",
            params={
                "project_id": project_id,
                "device_id": device_id,
                "limit": limit,
                "offset": offset,
            },
        )
        return WorkflowDeviceReferences.model_validate(
            await self._handle_response(response)
        )

    async def list_reconciliations(
        self,
        project_id: str,
        actor_staff_id: str,
        *,
        limit: int = 30,
        before_requested_at: str | None = None,
        before_id: str | None = None,
    ) -> list[WorkflowReconciliationSummary]:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        params: dict[str, str | int] = {
            "project_id": project_id,
            "actor_staff_id": actor_staff_id,
            "limit": limit,
        }
        if before_requested_at is not None and before_id is not None:
            params.update(
                before_requested_at=before_requested_at, before_id=before_id
            )
        response = await self._make_request(
            "GET",
            "/v1/workflows/approvals/reconciliations",
            params=params,
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        rows = await self._handle_response(response)
        return reconciliation_list(rows)

    async def get_reconciliation(
        self,
        project_id: str,
        actor_staff_id: str,
        approval_id: str,
    ) -> WorkflowReconciliationSummary:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        response = await self._make_request(
            "GET",
            f"/v1/workflows/approvals/reconciliations/{approval_id}",
            params={
                "project_id": project_id,
                "actor_staff_id": actor_staff_id,
            },
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        return reconciliation_contract(
            await self._handle_response(response),
            WorkflowReconciliationSummary,
        )

    async def decide_reconciliation(
        self,
        project_id: str,
        actor_staff_id: str,
        approval_id: str,
        decision: WorkflowReconciliationDecision,
    ) -> WorkflowReconciliationDecisionResult:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        response = await self._make_request(
            "POST",
            f"/v1/workflows/approvals/reconciliations/{approval_id}/decision",
            params={
                "project_id": project_id,
                "actor_staff_id": actor_staff_id,
            },
            json_data=decision.model_dump(mode="json"),
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        return reconciliation_contract(
            await self._handle_response(response),
            WorkflowReconciliationDecisionResult,
        )

    async def get_reconciliation_decision(
        self,
        project_id: str,
        actor_staff_id: str,
        approval_id: str,
        digest: str,
    ) -> WorkflowReconciliationDecisionResult:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        response = await self._make_request(
            "GET",
            f"/v1/workflows/approvals/reconciliations/{approval_id}"
            f"/decisions/{digest}",
            params={
                "project_id": project_id,
                "actor_staff_id": actor_staff_id,
            },
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        return reconciliation_contract(
            await self._handle_response(response),
            WorkflowReconciliationDecisionResult,
        )

    async def list_approvals(
        self,
        project_id: str,
        actor_staff_id: str,
    ) -> list[WorkflowApprovalSummary]:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        response = await self._make_request(
            "GET",
            "/v1/workflows/approvals",
            params={
                "project_id": project_id,
                "actor_staff_id": actor_staff_id,
            },
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        rows = await self._object_list_response(response)
        return [WorkflowApprovalSummary.model_validate(row) for row in rows]

    async def get_approval_customer_session(
        self,
        project_id: str,
        actor_staff_id: str,
        approval_id: str,
    ) -> WorkflowApprovalCustomerSession:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        response = await self._make_request(
            "GET",
            f"/v1/workflows/approvals/{approval_id}/customer-session",
            params={
                "project_id": project_id,
                "actor_staff_id": actor_staff_id,
            },
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        data = await self._handle_response(response)
        return WorkflowApprovalCustomerSession.model_validate(data)

    async def list_aftersales_reviews(
        self,
        project_id: str,
        actor_staff_id: str,
        *,
        limit: int = 100,
        before_status: str | None = None,
        before_created_at: str | None = None,
        before_id: str | None = None,
    ) -> list[WorkflowAfterSalesReviewSummary]:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        params: dict[str, str | int] = {
            "project_id": project_id,
            "actor_staff_id": actor_staff_id,
            "limit": limit,
        }
        if (
            before_status is not None
            and before_created_at is not None
            and before_id is not None
        ):
            params.update(
                {
                    "before_status": before_status,
                    "before_created_at": before_created_at,
                    "before_id": before_id,
                }
            )
        response = await self._make_request(
            "GET",
            "/v1/workflows/approvals/aftersales-reviews",
            params=params,
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        rows = await self._object_list_response(response)
        return [
            WorkflowAfterSalesReviewSummary.model_validate(row) for row in rows
        ]

    async def get_aftersales_review(
        self,
        project_id: str,
        actor_staff_id: str,
        review_id: str,
    ) -> WorkflowAfterSalesReviewSummary:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        response = await self._make_request(
            "GET",
            f"/v1/workflows/approvals/aftersales-reviews/{review_id}",
            params={
                "project_id": project_id,
                "actor_staff_id": actor_staff_id,
            },
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        row = await self._handle_response(response)
        return WorkflowAfterSalesReviewSummary.model_validate(row)

    async def get_aftersales_intake(
        self,
        project_id: str,
        actor_staff_id: str,
        review_id: str,
    ) -> WorkflowAfterSalesIntakeSummary | None:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        response = await self._make_request(
            "GET",
            f"/v1/workflows/approvals/aftersales-reviews/{review_id}/intake",
            params={
                "project_id": project_id,
                "actor_staff_id": actor_staff_id,
            },
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        row = await self._handle_response(response)
        return (
            None
            if row is None
            else WorkflowAfterSalesIntakeSummary.model_validate(row)
        )

    async def prepare_aftersales_intake(
        self,
        project_id: str,
        actor_staff_id: str,
        review_id: str,
        request: WorkflowAfterSalesIntakeCreate,
    ) -> WorkflowAfterSalesIntakeSummary:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        response = await self._make_request(
            "POST",
            f"/v1/workflows/approvals/aftersales-reviews/{review_id}/intake",
            params={
                "project_id": project_id,
                "actor_staff_id": actor_staff_id,
            },
            json_data=request.model_dump(mode="json"),
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        row = await self._handle_response(response)
        return WorkflowAfterSalesIntakeSummary.model_validate(row)

    async def decide_aftersales_review(
        self,
        project_id: str,
        actor_staff_id: str,
        review_id: str,
        decision: WorkflowAfterSalesReviewDecision,
    ) -> WorkflowAfterSalesReviewSummary:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        response = await self._make_request(
            "POST",
            f"/v1/workflows/approvals/aftersales-reviews/{review_id}/decision",
            params={
                "project_id": project_id,
                "actor_staff_id": actor_staff_id,
            },
            json_data=decision.model_dump(),
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        row = await self._handle_response(response)
        return WorkflowAfterSalesReviewSummary.model_validate(row)

    async def decide_approval(
        self,
        project_id: str,
        actor_staff_id: str,
        approval_id: str,
        decision: str,
        preview_digest: str,
    ) -> WorkflowApprovalDecisionResult:
        if not settings.SECRET_KEY:
            raise HTTPException(503, "工作流内部审批核验未配置")
        response = await self._make_request(
            "POST",
            f"/v1/workflows/approvals/{approval_id}/decision",
            params={
                "project_id": project_id,
                "actor_staff_id": actor_staff_id,
            },
            json_data={"decision": decision, "preview_digest": preview_digest},
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        row = await self._handle_response(response)
        result: WorkflowApprovalDecisionResult = (
            WorkflowApprovalDecisionResult.model_validate(row)
        )
        return result

    # Workflow endpoints (aligned with Workflow service OpenAPI: /v1/...)
    async def list_workflows(
        self,
        project_id: str,
        skip: int = 0,
        limit: int = 100,
        status: Optional[str] = None,
        search: Optional[str] = None,
        tags: Optional[List[str]] = None,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> JsonObject:
        params: RequestParams = {
            "project_id": project_id,
            "skip": skip,
            "limit": limit,
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
        if status is not None:
            params["status"] = status
        if search is not None:
            params["search"] = search
        if tags is not None:
            params["tags"] = tags
        response = await self._make_request(
            "GET", "/v1/workflows/", params=params
        )
        return await self._object_response(response)

    async def create_workflow(
        self, project_id: str, workflow_data: Mapping[str, object]
    ) -> JsonObject:
        params = {"project_id": project_id}
        response = await self._make_request(
            "POST", "/v1/workflows/", json_data=workflow_data, params=params
        )
        return await self._object_response(response)

    async def get_workflow(
        self, workflow_id: str, project_id: str
    ) -> JsonObject:
        params = {"project_id": project_id}
        response = await self._make_request(
            "GET", f"/v1/workflows/{workflow_id}", params=params
        )
        return await self._object_response(response)

    async def update_workflow(
        self,
        workflow_id: str,
        project_id: str,
        workflow_data: Mapping[str, object],
    ) -> JsonObject:
        params = {"project_id": project_id}
        response = await self._make_request(
            "PUT",
            f"/v1/workflows/{workflow_id}",
            json_data=workflow_data,
            params=params,
        )
        return await self._object_response(response)

    async def delete_workflow(self, workflow_id: str, project_id: str) -> None:
        params = {"project_id": project_id}
        response = await self._make_request(
            "DELETE", f"/v1/workflows/{workflow_id}", params=params
        )
        await self._handle_response(response)

    async def duplicate_workflow(
        self,
        workflow_id: str,
        project_id: str,
        request: Mapping[str, object] | None = None,
    ) -> JsonObject:
        params = {"project_id": project_id}
        response = await self._make_request(
            "POST",
            f"/v1/workflows/{workflow_id}/duplicate",
            json_data=request,
            params=params,
        )
        return await self._object_response(response)

    async def validate_workflow_generic(
        self, project_id: str, request: Mapping[str, object]
    ) -> JsonObject:
        params = {"project_id": project_id}
        response = await self._make_request(
            "POST", "/v1/workflows/validate", json_data=request, params=params
        )
        return await self._object_response(response)

    async def validate_workflow(
        self, workflow_id: str, project_id: str
    ) -> JsonObject:
        params = {"project_id": project_id}
        response = await self._make_request(
            "POST", f"/v1/workflows/{workflow_id}/validate", params=params
        )
        return await self._object_response(response)

    async def publish_workflow(
        self,
        workflow_id: str,
        project_id: str,
        request: WorkflowPublishRequest,
        staff_id: str,
    ) -> WorkflowInDB:
        params = {"project_id": project_id, "staff_id": staff_id}
        response = await self._make_request(
            "POST",
            f"/v1/workflows/{workflow_id}/publish",
            params=params,
            json_data=request.model_dump(mode="json"),
        )
        data = await self._handle_response(response)
        return WorkflowInDB.model_validate(data)

    async def workflow_releases(
        self,
        workflow_id: str,
        project_id: str,
    ) -> list[WorkflowReleaseSummary]:
        response = await self._make_request(
            "GET",
            f"/v1/workflows/{workflow_id}/releases",
            params={"project_id": project_id},
        )
        data = await self._object_list_response(response)
        return [WorkflowReleaseSummary.model_validate(row) for row in data]

    async def rollback_workflow(
        self,
        workflow_id: str,
        project_id: str,
        request: WorkflowRollbackRequest,
    ) -> WorkflowInDB:
        response = await self._make_request(
            "POST",
            f"/v1/workflows/{workflow_id}/rollback",
            params={"project_id": project_id},
            json_data=request.model_dump(mode="json"),
        )
        data = await self._handle_response(response)
        return WorkflowInDB.model_validate(data)

    async def get_workflow_variables(
        self, workflow_id: str, project_id: str
    ) -> JsonObject:
        params = {"project_id": project_id}
        response = await self._make_request(
            "GET", f"/v1/workflows/{workflow_id}/variables", params=params
        )
        return await self._object_response(response)

    async def execute_workflow(
        self,
        workflow_id: str,
        project_id: str,
        request: Mapping[str, object],
        *,
        staff_id: UUID | None = None,
        staff_token_version: int | None = None,
    ) -> JsonObject | AsyncGenerator[str, None]:
        """Execute workflow. Supports sync, async, and streaming modes."""
        url = f"{self.base_url}/v1/workflows/{workflow_id}/execute"
        params = {"project_id": project_id}
        headers = self._get_headers()
        headers["X-Request-ID"] = str(uuid4())
        authority: dict[str, str] = {}
        if staff_id is not None and staff_token_version is not None:
            authority = {
                "X-Internal-API-Key": settings.SECRET_KEY,
                "X-Workflow-Staff-ID": str(staff_id),
                "X-Workflow-Staff-Version": str(staff_token_version),
            }
        headers.update(authority)

        # Check if streaming is requested
        if request.get("stream"):

            async def stream_generator() -> AsyncGenerator[str, None]:
                async with httpx.AsyncClient(
                    timeout=None,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        url,
                        json=request,
                        params=params,
                        headers=headers,
                    ) as response:
                        if not response.is_success:
                            await response.aread()
                            error_detail = self._error_detail(response)
                            if isinstance(error_detail, dict):
                                error_detail = error_detail.get(
                                    "detail", error_detail
                                )
                            raise HTTPException(
                                status_code=response.status_code,
                                detail=error_detail,
                            )

                        async for line in response.aiter_lines():
                            if line:
                                yield f"{line}\n\n"

            return stream_generator()

        # Regular sync/async JSON response
        response = await self._make_request(
            "POST",
            f"/v1/workflows/{workflow_id}/execute",
            json_data=request,
            params=params,
            extra_headers=authority,
        )
        return await self._object_response(response)

    async def workflow_ai_context(
        self,
        command: WorkflowAICommand,
    ) -> WorkflowAIContext:
        response = await self._make_request(
            "GET",
            f"/v1/workflows/executions/{command.execution_id}/ai-context",
            params={
                "project_id": str(command.project_id),
                "node_id": command.node_id,
            },
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        data = await self._handle_response(response)
        return WorkflowAIContext.model_validate(data)

    async def workflow_ai_result(
        self,
        operation: Literal["prepare", "read", "store"],
        request: WorkflowAICacheCommand | WorkflowAICacheWrite,
    ) -> WorkflowAICacheReceipt:
        response = await self._make_request(
            "POST",
            "/v1/workflows/ai-runtime/results/" + operation,
            json_data=request.model_dump(mode="json"),
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
            timeout=15.0,
        )
        if response.status_code != 200:
            raise HTTPException(response.status_code, "原 AI 回答暂时无法核验。")
        try:
            return WorkflowAICacheReceipt.model_validate_json(response.content)
        except ValueError:
            raise HTTPException(502, "原 AI 回答回执无效。") from None

    async def workflow_usage_outcome(
        self,
        project_id: UUID,
        execution_id: UUID,
    ) -> WorkflowUsageOutcome:
        data = await self.get_execution(str(execution_id), str(project_id))
        from app.services.workflow_ai_usage import _has_output

        execution = WorkflowExecution.model_validate(data)
        outcome = WorkflowUsageOutcome.model_validate(
            {
                "project_id": data["project_id"],
                "execution_id": data["id"],
                "status": data["status"],
                "output": data.get("output"),
                "answer_available": any(
                    node.node_type == "answer"
                    and node.status == "completed"
                    and _has_output(node.output)
                    for node in execution.node_executions
                ),
            }
        )
        if (
            outcome.project_id != project_id
            or outcome.execution_id != execution_id
        ):
            raise HTTPException(502, "工作流用量核验范围不一致")
        return outcome

    async def get_execution(
        self, execution_id: str, project_id: str
    ) -> JsonObject:
        params = {"project_id": project_id}
        response = await self._make_request(
            "GET", f"/v1/workflows/executions/{execution_id}", params=params
        )
        return await self._object_response(response)

    async def list_workflow_executions(
        self,
        workflow_id: str,
        project_id: str,
        skip: int = 0,
        limit: int = 20,
        *,
        before_started_at: str | None = None,
        before_id: str | None = None,
    ) -> list[JsonObject]:
        params: RequestParams = {
            "project_id": project_id,
            "skip": skip,
            "limit": limit,
        }
        if before_started_at is not None:
            params["before_started_at"] = before_started_at
        if before_id is not None:
            params["before_id"] = before_id
        response = await self._make_request(
            "GET", f"/v1/workflows/{workflow_id}/executions", params=params
        )
        return await self._object_list_response(response)

    async def list_business_request_executions(
        self,
        business_request_id: str,
        project_id: str,
        *,
        limit: int = 20,
        visitor_session_id: str | None = None,
        skip: int = 0,
        before_started_at: str | None = None,
        before_id: str | None = None,
    ) -> list[WorkflowExecution]:
        params: RequestParams = {"project_id": project_id, "limit": limit}
        if skip:
            params["skip"] = skip
        if visitor_session_id is not None:
            params["visitor_session_id"] = visitor_session_id
        if before_started_at is not None:
            params["before_started_at"] = before_started_at
        if before_id is not None:
            params["before_id"] = before_id
        response = await self._make_request(
            "GET",
            "/v1/workflows/executions/by-business-request/"
            f"{business_request_id}",
            params=params,
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        data = await self._object_list_response(response)
        return [WorkflowExecution.model_validate(row) for row in data]

    async def list_visitor_session_executions(
        self,
        visitor_session_id: str,
        project_id: str,
        *,
        limit: int = 20,
        skip: int = 0,
        before_started_at: str | None = None,
        before_id: str | None = None,
    ) -> list[WorkflowExecution]:
        params: RequestParams = {"project_id": project_id, "limit": limit}
        if skip:
            params["skip"] = skip
        if before_started_at is not None:
            params["before_started_at"] = before_started_at
        if before_id is not None:
            params["before_id"] = before_id
        response = await self._make_request(
            "GET",
            "/v1/workflows/executions/by-visitor-session/"
            f"{visitor_session_id}",
            params=params,
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        data = await self._object_list_response(response)
        return [WorkflowExecution.model_validate(row) for row in data]

    async def list_child_executions(
        self,
        parent_execution_id: str,
        project_id: str,
        *,
        limit: int = 20,
        skip: int = 0,
        before_started_at: str | None = None,
        before_id: str | None = None,
    ) -> list[WorkflowExecution]:
        params: RequestParams = {"project_id": project_id, "limit": limit}
        if skip:
            params["skip"] = skip
        if before_started_at is not None:
            params["before_started_at"] = before_started_at
        if before_id is not None:
            params["before_id"] = before_id
        response = await self._make_request(
            "GET",
            f"/v1/workflows/executions/by-parent/{parent_execution_id}",
            params=params,
            extra_headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        data = await self._object_list_response(response)
        return [WorkflowExecution.model_validate(row) for row in data]

    async def cancel_execution(
        self, execution_id: str, project_id: str
    ) -> JsonObject:
        params = {"project_id": project_id}
        response = await self._make_request(
            "POST",
            f"/v1/workflows/executions/{execution_id}/cancel",
            params=params,
        )
        return await self._object_response(response)

    async def route_customer_service(
        self,
        routing_data: Mapping[str, object],
    ) -> JsonObject:
        """Apply the deterministic cross-service customer routing policy."""
        response = await self._make_request(
            "POST",
            "/v1/customer-service/route",
            json_data=routing_data,
        )
        result = await self._handle_response(response)
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=502,
                detail="Workflow routing service returned an invalid response",
            )
        return result


# Global workflow client instance
workflow_client = WorkflowServiceClient()
