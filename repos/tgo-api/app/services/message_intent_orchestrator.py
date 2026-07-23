"""End-to-end intent classification, persistence, and workflow routing."""

from __future__ import annotations

from dataclasses import dataclass
import json

from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.core.logging import get_logger
from app.models import (
    MessageIntentResult,
    Platform,
    Project,
    ProjectAIConfig,
    Visitor,
)
from app.schemas.message_analysis import IntentResultUpsertRequest
from app.services.ai_client import AIServiceClient
from app.services.message_analysis_service import MessageAnalysisService
from app.services.plugin_runtime_client import PluginRuntimeClient
from app.services.workflow_client import WorkflowServiceClient
from app.services.customer_logistics_service import CustomerLogisticsService


logger = get_logger("services.message_intent_orchestrator")

CLASSIFIER_VERSION = "tgo-ai-intent-v1"
POLICY_VERSION = "customer-service-routing-v1"


@dataclass(frozen=True)
class MessageIntentRoutingOutcome:
    """Persisted classification paired with the enforced workflow target."""

    intent_result: MessageIntentResult
    routing_target: str
    routing_reason: str
    tool_context: str | None = None


class MessageIntentOrchestrator:
    """Connect tgo-ai, tgo-api persistence, and tgo-workflow safely."""

    def __init__(
        self,
        db: Session,
        *,
        ai_client: AIServiceClient | None = None,
        workflow_client: WorkflowServiceClient | None = None,
        plugin_client: PluginRuntimeClient | None = None,
        logistics_service: CustomerLogisticsService | None = None,
    ) -> None:
        self._db = db
        self._ai_client = ai_client or AIServiceClient()
        self._workflow_client = workflow_client or WorkflowServiceClient()
        self._plugin_client = plugin_client or PluginRuntimeClient()
        self._logistics_service = logistics_service or CustomerLogisticsService(db)

    async def analyze_text_message(
        self,
        *,
        project: Project,
        platform: Platform,
        visitor: Visitor,
        source_message_id: str,
        user_text: str,
    ) -> MessageIntentRoutingOutcome:
        """Classify one text message, persist it, then enforce a workflow route."""
        normalized_text = user_text.strip()
        if not normalized_text:
            raise ValueError("user_text must not be empty")

        result_payload = await self._classify_or_fail_closed(
            project=project,
            user_text=normalized_text,
        )
        request = IntentResultUpsertRequest.model_validate(
            {
                **result_payload,
                "visitor_id": visitor.id,
                "classifier_version": CLASSIFIER_VERSION,
                "policy_version": POLICY_VERSION,
                "request_id": source_message_id,
            }
        )
        persisted = MessageAnalysisService(
            self._db
        ).upsert_intent_result_for_platform(
            platform=platform,
            source_message_id=source_message_id,
            request=request,
        )
        route = await self._route_or_handoff(request)
        tool_context: str | None = None
        if route["target"] == "read_only_tool":
            route, tool_context = await self._query_read_only_tool(
                project=project,
                visitor=visitor,
                source_message_id=source_message_id,
                request=request,
            )
        return MessageIntentRoutingOutcome(
            intent_result=persisted,
            routing_target=str(route["target"]),
            routing_reason=str(route["reason"]),
            tool_context=tool_context,
        )

    async def _classify_or_fail_closed(
        self,
        *,
        project: Project,
        user_text: str,
    ) -> dict[str, object]:
        config = (
            self._db.query(ProjectAIConfig)
            .filter(
                ProjectAIConfig.project_id == project.id,
                ProjectAIConfig.deleted_at.is_(None),
            )
            .first()
        )
        if (
            config is None
            or config.default_chat_provider_id is None
            or not config.default_chat_model
        ):
            logger.warning(
                "Intent classification failed closed: default chat model missing",
                extra={"project_id": str(project.id)},
            )
            return self._fail_closed_payload()

        try:
            return await self._ai_client.classify_intent(
                project_id=str(project.id),
                provider_id=str(config.default_chat_provider_id),
                model=config.default_chat_model,
                classification_input={"user_text": user_text},
            )
        except Exception as exc:
            logger.warning(
                "Intent classification failed closed",
                extra={"project_id": str(project.id), "error": str(exc)},
            )
            return self._fail_closed_payload()

    async def _route_or_handoff(
        self,
        request: IntentResultUpsertRequest,
    ) -> dict[str, object]:
        routing_payload: dict[str, object] = {
            "classification": {
                "intent": request.intent.value,
                "confidence": request.confidence,
                "risk_level": request.risk_level.value,
                "recommended_route": request.recommended_route.value,
                "need_human": request.need_human,
                "taxonomy_version": request.taxonomy_version,
            },
            "media_status": "not_applicable",
            "content_sources": ["user_text"],
            "content_trust_boundary": "untrusted_customer_content",
        }
        try:
            return await self._workflow_client.route_customer_service(
                routing_payload
            )
        except Exception as exc:
            logger.warning(
                "Workflow routing failed closed",
                extra={"error": str(exc)},
            )
            return {
                "target": "human_handoff",
                "reason": "workflow_service_unavailable",
            }

    async def _query_read_only_tool(
        self,
        *,
        project: Project,
        visitor: Visitor,
        source_message_id: str,
        request: IntentResultUpsertRequest,
    ) -> tuple[dict[str, object], str | None]:
        if request.intent.value == "logistics_query" and (
            request.entities.logistics_no or not request.entities.order_no
        ):
            logistics_outcome = await self._query_customer_logistics_archive(
                project=project,
                visitor=visitor,
                source_message_id=source_message_id,
                logistics_no=request.entities.logistics_no,
            )
            if logistics_outcome is not None:
                return logistics_outcome

        order_no = request.entities.order_no
        if not order_no:
            return (
                {"target": "clarify", "reason": "missing_order_or_tracking_number"},
                None,
            )
        operation = (
            "logistics_query"
            if request.intent.value == "logistics_query"
            else "order_query"
        )
        payload: dict[str, object] = {
            "context": {
                "tenant_id": str(project.id),
                "visitor_id": str(visitor.id),
                "external_customer_id": self._business_customer_id(visitor),
                "conversation_id": source_message_id,
                "request_id": source_message_id,
                "actor_id": "customer-service-agent",
            },
            "operation": operation,
            "order_no": order_no,
        }
        try:
            result = await self._plugin_client.query_business_data(payload)
        except Exception as exc:
            logger.warning(
                "Read-only business query failed closed",
                extra={"error": str(exc), "operation": operation},
            )
            return (
                {"target": "human_handoff", "reason": "business_tool_unavailable"},
                None,
            )
        if result is None:
            return (
                {"target": "human_handoff", "reason": "business_tool_unavailable"},
                None,
            )
        return (
            {"target": "read_only_tool", "reason": "read_only_query_completed"},
            "以下是只读业务系统返回的可信数据，只能据此回答，不得编造或执行写操作："
            + json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        )

    async def _query_customer_logistics_archive(
        self,
        *,
        project: Project,
        visitor: Visitor,
        source_message_id: str,
        logistics_no: str | None,
    ) -> tuple[dict[str, object], str | None] | None:
        """Use a supplied or previously archived tracking number for live lookup."""

        try:
            settings_row = self._logistics_service.get_settings(project.id)
            if not settings_row.enabled:
                return None
            if not logistics_no and not settings_row.auto_query_on_mention:
                return None
            if logistics_no:
                shipment = self._logistics_service.create_shipment(
                    project_id=project.id,
                    visitor_id=visitor.id,
                    tracking_no=logistics_no,
                    source="visitor_message",
                    source_message_id=source_message_id,
                )
            else:
                active = tuple(
                    shipment
                    for shipment in self._logistics_service.list_shipments(
                        project.id, visitor.id
                    )
                    if shipment.status not in {"delivered"}
                )
                if not active:
                    return None
                if len(active) > 1:
                    masked_numbers = "、".join(
                        shipment.tracking_no_masked for shipment in active[:5]
                    )
                    return (
                        {
                            "target": "clarify",
                            "reason": "multiple_active_tracking_numbers",
                        },
                        f"该顾客有多个进行中的物流单：{masked_numbers}，请确认要查询哪一个。",
                    )
                shipment = active[0]

            queried, events = await self._logistics_service.query_shipment(
                project.id, shipment.id
            )
        except HTTPException as exc:
            if exc.status_code == 409 and "尚未" in str(exc.detail):
                return (
                    {"target": "clarify", "reason": "logistics_tool_not_configured"},
                    "已找到顾客的物流档案，但还没有在物流设置中选择实时快递查询工具。",
                )
            logger.warning(
                "Customer logistics archive query failed",
                extra={"project_id": str(project.id), "error": str(exc.detail)},
            )
            return (
                {"target": "human_handoff", "reason": "logistics_query_failed"},
                None,
            )
        context = {
            "tracking_no": queried.tracking_no_masked,
            "carrier": queried.carrier_name,
            "status": queried.status,
            "latest_summary": queried.latest_summary,
            "events": [
                {
                    "status": event.status,
                    "description": event.description,
                    "location": event.location,
                    "event_time": event.event_time.isoformat(),
                }
                for event in events[:10]
            ],
        }
        return (
            {"target": "read_only_tool", "reason": "logistics_archive_query_completed"},
            "以下是顾客物流档案实时查询返回的可信数据，只能据此回答，不得编造："
            + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        )

    @staticmethod
    def _business_customer_id(visitor: Visitor) -> str:
        attributes = visitor.custom_attributes
        if isinstance(attributes, dict):
            configured_id = attributes.get("business_customer_id")
            if isinstance(configured_id, str) and configured_id.strip():
                return configured_id.strip()
        return visitor.platform_open_id

    @staticmethod
    def _fail_closed_payload() -> dict[str, object]:
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "entities": {},
            "risk_level": "high",
            "recommended_route": "human_handoff",
            "need_human": True,
            "taxonomy_version": "v1",
            "routing_reason": "classification_failed",
            "classification_source": "fail_closed",
        }
