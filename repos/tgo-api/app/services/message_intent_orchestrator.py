"""End-to-end intent classification, persistence, and workflow routing."""

from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.config import settings
from app.models import MessageIntentResult, Platform, Project, ProjectAIConfig, Visitor
from app.schemas.contextual_reply import ContextualReply
from app.schemas.humanization import ConversationTurn
from app.schemas.message_analysis import IntentResultUpsertRequest
from app.services.ai_client import AIServiceClient
from app.services.customer_logistics_service import CustomerLogisticsService
from app.services.customer_intelligence_service import CustomerIntelligenceService
from app.services.human_handoff_service import request_human_handoff
from app.services.humanization_service import recent_customer_messages
from app.services.message_analysis_service import MessageAnalysisService
from app.services.plugin_runtime_client import PluginRuntimeClient
from app.services.workflow_client import WorkflowServiceClient
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id

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
    excluded_tool_ids: tuple[str, ...] = ()
    handoff_result: dict[str, object] | None = None
    contextual_reply: ContextualReply | None = None


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
        self._intelligence_service = CustomerIntelligenceService(db)

    async def analyze_text_message(
        self,
        *,
        project: Project,
        platform: Platform,
        visitor: Visitor,
        source_message_id: str,
        user_text: str,
        reuse_existing: bool = False,
        defer_business_tools: bool = False,
        shadow_only: bool = False,
        conversation_context: list[str] | None = None,
        media_input: dict[str, object] | None = None,
    ) -> MessageIntentRoutingOutcome:
        """Classify one text message, persist it, then enforce a workflow route."""
        normalized_text = user_text.strip()
        if not normalized_text:
            raise ValueError("user_text must not be empty")

        persisted = None
        prior_turns: list[ConversationTurn] | None = None
        dependency: object = None
        faqs = settings.CONTEXTUAL_REPLY_CHANNELS.get(platform.id)
        if reuse_existing:
            persisted = self._db.query(MessageIntentResult).filter(
                MessageIntentResult.project_id == project.id,
                MessageIntentResult.platform_id == platform.id,
                MessageIntentResult.visitor_id == visitor.id,
                MessageIntentResult.source_message_id == source_message_id,
            ).first()
        if persisted is not None:
            request = IntentResultUpsertRequest.model_validate({
                "visitor_id": persisted.visitor_id,
                "media_analysis_result_id": persisted.media_analysis_result_id,
                "intent": persisted.intent,
                "confidence": persisted.confidence,
                "entities": persisted.entities,
                "risk_level": persisted.risk_level,
                "recommended_route": persisted.recommended_route,
                "need_human": persisted.need_human,
                "taxonomy_version": persisted.taxonomy_version,
                "routing_reason": persisted.routing_reason,
                "classification_source": persisted.classification_source,
                "classifier_version": persisted.classifier_version,
                "policy_version": persisted.policy_version,
                "request_id": persisted.request_id,
            })
        else:
            if faqs is not None:
                channel_id = build_visitor_channel_id(visitor.id)
                try:
                    prior_turns = await recent_customer_messages(
                        channel_id, CHANNEL_TYPE_CUSTOMER_SERVICE, channel_id,
                        before_source_message_id=source_message_id,
                    )
                except Exception as exc:
                    logger.info(
                        "Contextual routing history unavailable",
                        extra={"error_type": type(exc).__name__},
                    )
            result_payload = await self._classify_or_fail_closed(
                project=project,
                user_text=normalized_text,
                prior_turns=prior_turns,
                conversation_context=conversation_context,
                media_input=media_input,
            )
            dependency = result_payload.pop("context_dependency", None)
            request = IntentResultUpsertRequest.model_validate({
                **result_payload,
                "visitor_id": visitor.id,
                "classifier_version": CLASSIFIER_VERSION,
                "policy_version": POLICY_VERSION,
                "request_id": source_message_id,
            })
            persisted = MessageAnalysisService(self._db).upsert_intent_result_for_platform(
                platform=platform,
                source_message_id=source_message_id,
                request=request,
                analysis_mode="shadow" if shadow_only else "active",
            )
            try:
                self._intelligence_service.record_intent(persisted)
            except Exception as exc:
                self._db.rollback()
                logger.warning(
                    "Customer intelligence projection failed without blocking chat",
                    extra={
                        "visitor_id": str(visitor.id),
                        "source_message_id": source_message_id,
                        "error_type": type(exc).__name__,
                    },
                )
        if shadow_only or persisted.analysis_mode == "shadow":
            # Assist-mode shadow analysis stops before workflow, business tools,
            # and human handoff. The staff remains responsible for the reply.
            return MessageIntentRoutingOutcome(
                intent_result=persisted,
                routing_target="shadow_only",
                routing_reason="shadow_analysis_only",
            )
        route = await self._route_or_handoff(request)
        contextual_reply = None
        if faqs is not None and route["target"] in ("rag", "clarify"):
            summary = request.entities.issue_summary
            if dependency == "ambiguous" or (
                dependency == "follow_up" and not summary
            ):
                route = {"target": "clarify", "reason": "context_ambiguous"}
            elif dependency == "follow_up" and summary:
                contextual_reply = ContextualReply(lookup_question=summary)
            elif (
                dependency == "standalone" and route["target"] == "rag"
                and request.intent.value == "product_inquiry"
                and request.risk_level.value == "low"
                and request.confidence >= 0.85
                and not (
                    settings.SAAS_ENABLED and settings.SAAS_BILLING_ENABLED
                )
                and prior_turns is not None
                and all(len(turn.content) <= 600 for turn in prior_turns)
            ):
                matches = [
                    entry for entry in faqs if entry.answers(normalized_text)
                ]
                if len(matches) == 1:
                    contextual_reply = ContextualReply(reviewed_faq=matches[0])
            logger.info("Contextual reply classified", extra={
                "source_message_id": source_message_id,
                "context_dependency": dependency,
                "prior_turns": (
                    len(prior_turns) if prior_turns is not None else None
                ),
                "reviewed_faq_candidate": bool(
                    contextual_reply and contextual_reply.reviewed_faq
                ),
            })
        tool_context: str | None = None
        excluded_tool_ids: tuple[str, ...] = ()
        if route["target"] == "read_only_tool" and defer_business_tools:
            # Presence only defers classification's legacy lookup. The signed
            # context and employee permission are checked by the formal run.
            route = {"target": "auto_reply", "reason": "employee_business_execution"}
        elif route["target"] == "read_only_tool":
            route, tool_context, excluded_tool_ids = await self._query_read_only_tool(
                project=project,
                visitor=visitor,
                source_message_id=source_message_id,
                request=request,
            )
        handoff_result: dict[str, object] | None = None
        if route["target"] == "human_handoff":
            handoff_result = await request_human_handoff(
                db=self._db,
                project=project,
                visitor=visitor,
                reason=normalized_text,
                source_message_id=source_message_id,
                strict_source_receipt=True,
                routing_reason=str(route["reason"]),
                channel=platform.type,
            )
        return MessageIntentRoutingOutcome(
            intent_result=persisted,
            routing_target=str(route["target"]),
            routing_reason=str(route["reason"]),
            tool_context=tool_context,
            excluded_tool_ids=excluded_tool_ids,
            handoff_result=handoff_result,
            contextual_reply=contextual_reply,
        )

    async def _classify_or_fail_closed(
        self,
        *,
        project: Project,
        user_text: str,
        prior_turns: list[ConversationTurn] | None = None,
        conversation_context: list[str] | None = None,
        media_input: dict[str, object] | None = None,
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
            classification_input: dict[str, object] = {"user_text": user_text, **(media_input or {}), "conversation_context": (conversation_context or [])[-12:]}
            if prior_turns is not None:
                classification_input["prior_turns"] = [
                    {"role": turn.role, "content": turn.content[:600]}
                    for turn in prior_turns
                ]
            return await self._ai_client.classify_intent(
                project_id=str(project.id),
                provider_id=str(config.default_chat_provider_id),
                model=config.default_chat_model,
                classification_input=classification_input,
            )
        except Exception as exc:
            logger.warning(
                "Intent classification failed closed",
                extra={
                    "project_id": str(project.id),
                    "error_type": type(exc).__name__,
                },
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
            return await self._workflow_client.route_customer_service(routing_payload)
        except Exception as exc:
            logger.warning(
                "Workflow routing failed closed",
                extra={"error_type": type(exc).__name__},
            )
            if (
                request.recommended_route.value == "read_only_tool"
                and request.intent.value in {"order_query", "logistics_query"}
            ):
                return {
                    "target": "clarify",
                    "reason": "workflow_service_unavailable",
                }
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
    ) -> tuple[dict[str, object], str | None, tuple[str, ...]]:
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
                (),
            )
        # A platform visitor key or an editable customer attribute cannot prove
        # merchant identity or employee permission. Formal tools own that path.
        return (
            {"target": "clarify", "reason": "verified_business_context_required"},
            None,
            (),
        )

    async def _query_customer_logistics_archive(
        self,
        *,
        project: Project,
        visitor: Visitor,
        source_message_id: str,
        logistics_no: str | None,
    ) -> tuple[dict[str, object], str | None, tuple[str, ...]] | None:
        """Use a supplied or previously archived tracking number for live lookup."""

        try:
            settings_row = self._logistics_service.get_settings(project.id)
            if not settings_row.enabled:
                return (
                    {"target": "clarify", "reason": "logistics_tool_not_configured"},
                    "物流查询暂未启用，请保留快递单号并稍后再试。",
                    (),
                )
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
                        (),
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
                    (),
                )
            logger.warning(
                "Customer logistics archive query failed",
                extra={"project_id": str(project.id), "error_type": type(exc).__name__},
            )
            return (
                {"target": "clarify", "reason": "logistics_query_failed"},
                "物流查询暂时失败，请核对快递单号或稍后再试。",
                (),
            )
        except Exception as exc:
            logger.warning(
                "Customer logistics archive query failed",
                extra={"project_id": str(project.id), "error_type": type(exc).__name__},
            )
            return (
                {"target": "clarify", "reason": "logistics_query_failed"},
                "物流查询暂时失败，请核对快递单号或稍后再试。",
                (),
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
            (str(settings_row.query_tool_id),)
            if settings_row.query_tool_id is not None
            else (),
        )

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
