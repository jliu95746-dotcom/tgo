"""End-to-end contract for intent analysis orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.contextual_reply_config import ReviewedFAQ
from app.models import (
    MediaAnalysisResult,
    MessageIntentResult,
    Platform,
    Project,
    ProjectAIConfig,
    Visitor,
)
from app.services.ai_client import AIServiceClient
from app.services.message_intent_orchestrator import MessageIntentOrchestrator
from app.schemas.humanization import ConversationTurn


class _Query:
    def __init__(self, session: _Session, model: type[object]) -> None:
        self._session = session
        self._model = model

    def filter(self, *_criteria: object) -> _Query:
        return self

    def first(self) -> object | None:
        if self._model is ProjectAIConfig:
            return self._session.config
        if self._model is Visitor:
            return self._session.visitor
        if self._model is MessageIntentResult:
            return self._session.intent
        if self._model is MediaAnalysisResult:
            return None
        raise AssertionError(f"unexpected query: {self._model}")


class _Session:
    def __init__(self, config: ProjectAIConfig, visitor: Visitor) -> None:
        self.config = config
        self.visitor = visitor
        self.intent: MessageIntentResult | None = None

    def query(self, model: type[object]) -> _Query:
        return _Query(self, model)

    def add(self, value: object) -> None:
        assert isinstance(value, MessageIntentResult)
        value.id = uuid4()
        self.intent = value

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def refresh(self, value: object) -> None:
        assert isinstance(value, MessageIntentResult)
        now = datetime.now(timezone.utc)
        value.created_at = now
        value.updated_at = now


class _AIClient:
    async def classify_intent(self, **_kwargs: object) -> dict[str, object]:
        return {
            "intent": "logistics_query",
            "confidence": 0.96,
            "entities": {"order_no": "ORDER-20260716-001"},
            "risk_level": "low",
            "recommended_route": "read_only_tool",
            "need_human": False,
            "taxonomy_version": "v1",
            "routing_reason": "high_confidence_read_only",
            "classification_source": "model",
        }


class _WorkflowClient:
    async def route_customer_service(
        self, routing_data: dict[str, object]
    ) -> dict[str, object]:
        assert routing_data["media_status"] == "not_applicable"
        return {"target": "read_only_tool", "reason": "read_only_query"}


class _UnavailableWorkflowClient:
    async def route_customer_service(
        self, _routing_data: dict[str, object]
    ) -> dict[str, object]:
        raise RuntimeError("private-workflow-response")


class _PluginClient:
    last_request: dict[str, object] | None = None

    async def query_business_data(
        self, request_data: dict[str, object]
    ) -> dict[str, object]:
        self.last_request = request_data
        assert request_data["operation"] == "logistics_query"
        return {
            "operation": "logistics_query",
            "logistics": {
                "order_no": "ORDER-20260716-001",
                "status": "运输中",
                "carrier": "顺丰速运",
                "tracking_no_masked": "SF****5678",
            },
        }


class _UnexpectedAIClient:
    def __init__(self) -> None:
        self.called = False

    async def classify_intent(self, **_kwargs: object) -> dict[str, object]:
        self.called = True
        raise AssertionError("safe auto-reply text must not invoke the intent model")


class _AutoReplyWorkflowClient:
    def __init__(self) -> None:
        self.last_request: dict[str, object] | None = None

    async def route_customer_service(
        self, routing_data: dict[str, object]
    ) -> dict[str, object]:
        self.last_request = routing_data
        return {"target": "auto_reply", "reason": "high_confidence_faq"}


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency,history_ok,expected", [
    ("standalone", True, "fast"),
    ("follow_up", True, "full"),
    ("standalone", False, None),
    ("ambiguous", True, None),
])
async def test_contextual_reply_requires_independence_and_reviewed_match(
    monkeypatch, dependency, history_ok, expected,
) -> None:
    project = Project(id=uuid4(), name="fixture", api_key="fixture")
    platform = Platform(
        id=uuid4(), project_id=project.id, type="website", is_active=True,
    )
    visitor = Visitor(id=uuid4(), project_id=project.id, platform_id=platform.id)
    config = ProjectAIConfig(
        project_id=project.id, default_chat_provider_id=uuid4(),
        default_chat_model="fixture",
    )
    question = "A103 通勤包的肩带多长？"
    faq = ReviewedFAQ(
        question=question, subject="A103 通勤包", answer="120 厘米。",
        valid_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(settings, "CONTEXTUAL_REPLY_CHANNELS", {platform.id: [faq]})
    monkeypatch.setattr(settings, "SAAS_ENABLED", False)
    classifier = AsyncMock(return_value={
        "intent": "product_inquiry", "confidence": 0.97,
        "entities": {"product_name": "A103", "issue_summary": "A103 黑色现货"},
        "risk_level": "low", "recommended_route": "auto_reply", "need_human": False,
        "taxonomy_version": "v1", "routing_reason": "high_confidence_faq",
        "classification_source": "model", "context_dependency": dependency,
    })
    service = MessageIntentOrchestrator(
        _Session(config, visitor),  # type: ignore[arg-type]
        ai_client=SimpleNamespace(classify_intent=classifier),
        workflow_client=SimpleNamespace(route_customer_service=AsyncMock(
            return_value={"target": "rag", "reason": "reviewed_knowledge"},
        )),
    )
    with patch(
        "app.services.message_intent_orchestrator.recent_customer_messages",
        new=AsyncMock(
            return_value=[ConversationTurn(role="customer", content="只问黑色现货")],
            side_effect=None if history_ok else OSError("fixture"),
        ),
    ) as history:
        outcome = await service.analyze_text_message(
            project=project, platform=platform, visitor=visitor,
            source_message_id="source-id", user_text=question,
        )
    assert history.await_args.kwargs["before_source_message_id"] == "source-id"
    model_input = classifier.await_args.kwargs["classification_input"]
    assert ("prior_turns" in model_input) is history_ok
    if expected == "fast":
        assert outcome.contextual_reply.reviewed_faq == faq
    elif expected == "full":
        assert outcome.contextual_reply.reviewed_faq is None
        assert outcome.contextual_reply.lookup_question == "A103 黑色现货"
    else:
        assert outcome.contextual_reply is None


@pytest.mark.asyncio
async def test_history_retries_a_late_boundary_and_excludes_later_messages(
    monkeypatch,
) -> None:
    from app.services.humanization_service import recent_customer_messages
    from app.services.wukongim_client import wukongim_client

    def message(sequence, content, source_id=None):
        return SimpleNamespace(
            message_seq=sequence, from_uid="fixture-vtr", message_id=sequence,
            client_msg_no=f"fixture-{sequence}", payload={
                "type": 1, "content": content, "extra": {"message_id": source_id},
            },
        )

    prior = message(1, "A103 周末广州门店自提库存")
    current = message(2, "黑色呢？", "fixture-current")
    later = message(3, "B208 另一个问题")
    sync = AsyncMock(side_effect=[
        SimpleNamespace(messages=[prior]),
        SimpleNamespace(messages=[later, current, prior]),
    ])
    monkeypatch.setattr(wukongim_client, "sync_channel_messages", sync)
    turns = await recent_customer_messages(
        "fixture-vtr", 251, "fixture-vtr", before_source_message_id="fixture-current",
    )
    assert [turn.content for turn in turns] == ["A103 周末广州门店自提库存"]
    assert sync.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    (
        "请简单介绍一下你们的功能",
        "我不想活了",
        "物流单号 SF1234567890，我要投诉",
        "请帮我查询订单",
        "我想转人工客服",
        "这个商品坏了，怎么售后",
        "账号被盗了",
        "请帮我退！款",
        "手机号 15110345442",
        "SF1234567890",
        "321143976686919",
    ),
)
async def test_customer_text_uses_semantic_classification_before_routing(
    message: str,
) -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    visitor = Visitor(
        id=uuid4(), project_id=project.id, platform_id=uuid4(),
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(
        project_id=project.id, default_chat_provider_id=uuid4(),
        default_chat_model="test-model",
    )
    ai_client = SimpleNamespace(classify_intent=AsyncMock(return_value={
        "intent": "product_inquiry", "confidence": 0.9, "entities": {},
        "risk_level": "low", "recommended_route": "auto_reply",
        "need_human": False, "taxonomy_version": "v1",
        "routing_reason": "high_confidence_faq", "classification_source": "model",
    }))
    orchestrator = MessageIntentOrchestrator(
        _Session(config, visitor),  # type: ignore[arg-type]
        ai_client=ai_client,  # type: ignore[arg-type]
    )

    result = await orchestrator._classify_or_fail_closed(
        project=project, user_text=message,
    )

    ai_client.classify_intent.assert_awaited_once()
    assert result["classification_source"] == "model"


@pytest.mark.asyncio
async def test_intent_provider_error_fails_closed_without_logging_response(
    caplog,
) -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    visitor = Visitor(
        id=uuid4(), project_id=project.id, platform_id=uuid4(),
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(
        project_id=project.id, default_chat_provider_id=uuid4(),
        default_chat_model="test-model",
    )
    ai_client = SimpleNamespace(classify_intent=AsyncMock(
        side_effect=RuntimeError("private-model-response")
    ))
    orchestrator = MessageIntentOrchestrator(
        _Session(config, visitor),  # type: ignore[arg-type]
        ai_client=ai_client,  # type: ignore[arg-type]
    )

    result = await orchestrator._classify_or_fail_closed(
        project=project, user_text="咨询商品",
    )

    assert result["recommended_route"] == "human_handoff"
    assert result["classification_source"] == "fail_closed"
    assert all(
        "private-model-response" not in str(record.__dict__)
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_channel_handoff_uses_source_message_receipt() -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(), project_id=project.id, name="wecom", type="wecom",
        api_key="platform-key", is_active=True,
    )
    visitor = Visitor(
        id=uuid4(), project_id=project.id, platform_id=platform.id,
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(
        project_id=project.id, default_chat_provider_id=uuid4(),
        default_chat_model="test-model",
    )
    orchestrator = MessageIntentOrchestrator(
        _Session(config, visitor),  # type: ignore[arg-type]
        ai_client=_AIClient(),  # type: ignore[arg-type]
        workflow_client=_WorkflowClient(),  # type: ignore[arg-type]
    )
    with (
        patch.object(orchestrator, "_route_or_handoff", new=AsyncMock(
            return_value={"target": "human_handoff", "reason": "high_risk"},
        )),
        patch(
            "app.services.message_intent_orchestrator.request_human_handoff",
            new=AsyncMock(return_value={"request_id": "handoff-1"}),
        ) as handoff,
    ):
        await orchestrator.analyze_text_message(
            project=project, platform=platform, visitor=visitor,
            source_message_id="wecom-message-1", user_text="请转人工",
        )
    assert handoff.await_args.kwargs["source_message_id"] == "wecom-message-1"
    assert handoff.await_args.kwargs["strict_source_receipt"] is True


@pytest.mark.asyncio
async def test_assist_shadow_persists_intent_without_workflow_tool_or_handoff() -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(), project_id=project.id, name="wecom", type="wecom",
        api_key="platform-key", is_active=True, ai_mode="assist",
    )
    visitor = Visitor(
        id=uuid4(), project_id=project.id, platform_id=platform.id,
        platform_open_id="visitor-open-id", service_mode="assist",
    )
    config = ProjectAIConfig(
        project_id=project.id, default_chat_provider_id=uuid4(),
        default_chat_model="test-model",
    )
    session = _Session(config, visitor)
    workflow = SimpleNamespace(
        route_customer_service=AsyncMock(
            side_effect=AssertionError("shadow must not route")
        )
    )
    orchestrator = MessageIntentOrchestrator(
        session,  # type: ignore[arg-type]
        ai_client=_AIClient(),  # type: ignore[arg-type]
        workflow_client=workflow,  # type: ignore[arg-type]
    )

    outcome = await orchestrator.analyze_text_message(
        project=project, platform=platform, visitor=visitor,
        source_message_id="wecom-shadow-1", user_text="请帮我查询订单",
        shadow_only=True,
    )

    assert outcome.intent_result is session.intent
    assert outcome.intent_result.analysis_mode == "shadow"
    assert outcome.routing_target == "shadow_only"
    assert outcome.routing_reason == "shadow_analysis_only"
    assert outcome.tool_context is None
    assert outcome.handoff_result is None
    workflow.route_customer_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_reuses_persisted_intent_without_reclassifying() -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(), project_id=project.id, name="wecom", type="wecom",
        api_key="platform-key", is_active=True,
    )
    visitor = Visitor(
        id=uuid4(), project_id=project.id, platform_id=platform.id,
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(
        project_id=project.id, default_chat_provider_id=uuid4(),
        default_chat_model="deepseek-v4-flash",
    )
    session = _Session(config, visitor)
    saved = MessageIntentResult(
        id=uuid4(), project_id=project.id, platform_id=platform.id,
        visitor_id=visitor.id, source_message_id="wecom-message-1",
        intent="order_query", confidence=0.92, entities={}, risk_level="low",
        recommended_route="auto_reply", need_human=False,
        taxonomy_version="v1", routing_reason="high_confidence_faq",
        classification_source="model", classifier_version="tgo-ai-intent-v1",
        policy_version="customer-service-routing-v1", input_fingerprint="stored",
        request_id="wecom-message-1", analysis_mode="active",
    )
    session.intent = saved
    ai_client = _UnexpectedAIClient()
    orchestrator = MessageIntentOrchestrator(
        session,  # type: ignore[arg-type]
        ai_client=ai_client,  # type: ignore[arg-type]
        workflow_client=_AutoReplyWorkflowClient(),  # type: ignore[arg-type]
    )

    outcome = await orchestrator.analyze_text_message(
        project=project, platform=platform, visitor=visitor,
        source_message_id="wecom-message-1", user_text="订单情况怎么样？",
        reuse_existing=True,
    )

    assert ai_client.called is False
    assert outcome.intent_result is saved
    assert outcome.intent_result.analysis_mode == "active"
    assert outcome.routing_target == "auto_reply"


@pytest.mark.asyncio
async def test_replaying_shadow_result_cannot_start_auto_workflow() -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(), project_id=project.id, name="web", type="website",
        api_key="platform-key", is_active=True,
    )
    visitor = Visitor(
        id=uuid4(), project_id=project.id, platform_id=platform.id,
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(project_id=project.id)
    session = _Session(config, visitor)
    saved = MessageIntentResult(
        id=uuid4(), project_id=project.id, platform_id=platform.id,
        visitor_id=visitor.id, source_message_id="shadow-message-1",
        analysis_mode="shadow", intent="product_inquiry", confidence=0.99,
        entities={}, risk_level="low", recommended_route="auto_reply",
        need_human=False, taxonomy_version="v1",
        routing_reason="high_confidence_faq", classification_source="rule",
        classifier_version="tgo-ai-intent-v1", policy_version="routing-v1",
        input_fingerprint="saved", request_id="shadow-message-1",
    )
    session.intent = saved
    workflow = SimpleNamespace(route_customer_service=AsyncMock(
        side_effect=AssertionError("a shadow result must not start a workflow")
    ))
    orchestrator = MessageIntentOrchestrator(
        session,  # type: ignore[arg-type]
        ai_client=_UnexpectedAIClient(),  # type: ignore[arg-type]
        workflow_client=workflow,  # type: ignore[arg-type]
    )

    outcome = await orchestrator.analyze_text_message(
        project=project, platform=platform, visitor=visitor,
        source_message_id="shadow-message-1", user_text="这款包怎么样？",
        reuse_existing=True,
    )

    assert outcome.routing_target == "shadow_only"
    workflow.route_customer_service.assert_not_awaited()


class _LogisticsAIClient:
    async def classify_intent(self, **_kwargs: object) -> dict[str, object]:
        return {
            "intent": "logistics_query",
            "confidence": 0.98,
            "entities": {"logistics_no": "SF1234567890"},
            "risk_level": "low",
            "recommended_route": "read_only_tool",
            "need_human": False,
            "taxonomy_version": "v1",
            "routing_reason": "high_confidence_read_only",
            "classification_source": "model",
        }


class _LogisticsService:
    def __init__(self) -> None:
        self.query_tool_id = uuid4()
        self.shipment = SimpleNamespace(
            id=uuid4(),
            tracking_no_masked="SF12****7890",
            carrier_name="顺丰速运",
            status="in_transit",
            latest_summary="快件正在运输中",
        )

    def get_settings(self, _project_id):
        return SimpleNamespace(
            enabled=True,
            auto_query_on_mention=True,
            query_tool_id=self.query_tool_id,
        )

    def create_shipment(self, **_kwargs):
        return self.shipment

    async def query_shipment(self, _project_id, _shipment_id):
        return self.shipment, ()


class _FailingLogisticsService(_LogisticsService):
    def __init__(self, failure: Exception) -> None:
        super().__init__()
        self.failure = failure

    async def query_shipment(self, _project_id, _shipment_id):
        raise self.failure


@pytest.mark.asyncio
async def test_normal_customer_message_uses_intent_model() -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(),
        project_id=project.id,
        name="web",
        type="website",
        api_key="platform-key",
        is_active=True,
    )
    visitor = Visitor(
        id=uuid4(),
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(
        project_id=project.id,
        default_chat_provider_id=uuid4(),
        default_chat_model="deepseek-v4-flash",
    )
    session = _Session(config, visitor)
    ai_client = SimpleNamespace(classify_intent=AsyncMock(return_value={
        "intent": "product_inquiry", "confidence": 0.92, "entities": {},
        "risk_level": "low", "recommended_route": "auto_reply",
        "need_human": False, "taxonomy_version": "v1",
        "routing_reason": "high_confidence_faq", "classification_source": "model",
    }))
    workflow_client = _AutoReplyWorkflowClient()
    orchestrator = MessageIntentOrchestrator(
        session,  # type: ignore[arg-type]
        ai_client=ai_client,  # type: ignore[arg-type]
        workflow_client=workflow_client,  # type: ignore[arg-type]
    )

    outcome = await orchestrator.analyze_text_message(
        project=project,
        platform=platform,
        visitor=visitor,
        source_message_id="user-message-fast-path",
        user_text="你好",
    )

    ai_client.classify_intent.assert_awaited_once()
    assert outcome.intent_result.intent == "product_inquiry"
    assert outcome.intent_result.classification_source == "model"
    assert outcome.routing_target == "auto_reply"
    assert workflow_client.last_request is not None


@pytest.mark.asyncio
async def test_message_is_classified_persisted_and_routed() -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(),
        project_id=project.id,
        name="web",
        type="website",
        api_key="platform-key",
        is_active=True,
    )
    visitor = Visitor(
        id=uuid4(),
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(
        project_id=project.id,
        default_chat_provider_id=uuid4(),
        default_chat_model="deepseek-v4-flash",
    )
    session = _Session(config, visitor)
    plugin_client = _PluginClient()
    orchestrator = MessageIntentOrchestrator(
        session,  # type: ignore[arg-type]
        ai_client=_AIClient(),  # type: ignore[arg-type]
        workflow_client=_WorkflowClient(),  # type: ignore[arg-type]
        plugin_client=plugin_client,  # type: ignore[arg-type]
    )

    outcome = await orchestrator.analyze_text_message(
        project=project,
        platform=platform,
        visitor=visitor,
        source_message_id="user-message-1",
        user_text="订单 ORDER-20260716-001 到哪里了？",
    )

    assert outcome.intent_result.intent == "logistics_query"
    assert outcome.intent_result.entities["order_no"] == "ORDER-20260716-001"
    assert outcome.routing_target == "clarify"
    assert outcome.routing_reason == "verified_business_context_required"
    assert outcome.tool_context is None
    assert session.intent is outcome.intent_result
    assert plugin_client.last_request is None


@pytest.mark.asyncio
async def test_self_declared_business_customer_id_cannot_authorize_a_query() -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(),
        project_id=project.id,
        name="web",
        type="website",
        api_key="platform-key",
        is_active=True,
    )
    visitor = Visitor(
        id=uuid4(),
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id="visitor-open-id",
        custom_attributes={"business_customer_id": "member-7788"},
    )
    config = ProjectAIConfig(
        project_id=project.id,
        default_chat_provider_id=uuid4(),
        default_chat_model="deepseek-v4-flash",
    )
    plugin_client = _PluginClient()
    orchestrator = MessageIntentOrchestrator(
        _Session(config, visitor),  # type: ignore[arg-type]
        ai_client=_AIClient(),  # type: ignore[arg-type]
        workflow_client=_WorkflowClient(),  # type: ignore[arg-type]
        plugin_client=plugin_client,  # type: ignore[arg-type]
    )

    await orchestrator.analyze_text_message(
        project=project,
        platform=platform,
        visitor=visitor,
        source_message_id="user-message-2",
        user_text="订单 ORDER-20260716-001 到哪里了？",
    )

    assert plugin_client.last_request is None


@pytest.mark.asyncio
async def test_logistics_archive_excludes_the_tool_already_executed() -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(),
        project_id=project.id,
        name="web",
        type="website",
        api_key="platform-key",
        is_active=True,
    )
    visitor = Visitor(
        id=uuid4(),
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(
        project_id=project.id,
        default_chat_provider_id=uuid4(),
        default_chat_model="deepseek-v4-flash",
    )
    logistics_service = _LogisticsService()
    orchestrator = MessageIntentOrchestrator(
        _Session(config, visitor),  # type: ignore[arg-type]
        ai_client=_LogisticsAIClient(),  # type: ignore[arg-type]
        workflow_client=_WorkflowClient(),  # type: ignore[arg-type]
        logistics_service=logistics_service,  # type: ignore[arg-type]
    )

    outcome = await orchestrator.analyze_text_message(
        project=project,
        platform=platform,
        visitor=visitor,
        source_message_id="user-message-logistics",
        user_text="帮我查一下 SF1234567890",
    )

    assert outcome.routing_target == "read_only_tool"
    assert outcome.excluded_tool_ids == (str(logistics_service.query_tool_id),)


@pytest.mark.asyncio
async def test_tracking_number_query_uses_model_before_read_only_route() -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(),
        project_id=project.id,
        name="web",
        type="website",
        api_key="platform-key",
        is_active=True,
    )
    visitor = Visitor(
        id=uuid4(),
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(
        project_id=project.id,
        default_chat_provider_id=uuid4(),
        default_chat_model="deepseek-v4-flash",
    )
    ai_client = SimpleNamespace(
        classify_intent=AsyncMock(wraps=_LogisticsAIClient().classify_intent)
    )
    orchestrator = MessageIntentOrchestrator(
        _Session(config, visitor),  # type: ignore[arg-type]
        ai_client=ai_client,  # type: ignore[arg-type]
        workflow_client=_WorkflowClient(),  # type: ignore[arg-type]
        logistics_service=_LogisticsService(),  # type: ignore[arg-type]
    )

    outcome = await orchestrator.analyze_text_message(
        project=project,
        platform=platform,
        visitor=visitor,
        source_message_id="user-message-explicit-tracking",
        user_text="快递单号 SF1234567890",
    )

    ai_client.classify_intent.assert_awaited_once()
    assert outcome.intent_result.intent == "logistics_query"
    assert outcome.intent_result.classification_source == "model"
    assert outcome.routing_target == "read_only_tool"


@pytest.mark.asyncio
async def test_tracking_number_workflow_failure_does_not_handoff(caplog) -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(),
        project_id=project.id,
        name="web",
        type="website",
        api_key="platform-key",
        is_active=True,
    )
    visitor = Visitor(
        id=uuid4(),
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(
        project_id=project.id,
        default_chat_provider_id=uuid4(),
        default_chat_model="deepseek-v4-flash",
    )
    orchestrator = MessageIntentOrchestrator(
        _Session(config, visitor),  # type: ignore[arg-type]
        ai_client=_LogisticsAIClient(),  # type: ignore[arg-type]
        workflow_client=_UnavailableWorkflowClient(),  # type: ignore[arg-type]
        logistics_service=_LogisticsService(),  # type: ignore[arg-type]
    )

    outcome = await orchestrator.analyze_text_message(
        project=project,
        platform=platform,
        visitor=visitor,
        source_message_id="user-message-workflow-unavailable",
        user_text="快递单号 SF1234567890",
    )

    assert outcome.routing_target == "clarify"
    assert outcome.routing_reason == "workflow_service_unavailable"
    assert outcome.handoff_result is None
    assert all(
        "private-workflow-response" not in str(record.__dict__)
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", (
    HTTPException(status_code=502, detail="private-logistics-response"),
    RuntimeError("private-logistics-response"),
))
async def test_logistics_query_failure_clarifies_without_handoff(
    caplog, failure: Exception,
) -> None:
    project = Project(id=uuid4(), name="test", api_key="ak_test")
    platform = Platform(
        id=uuid4(),
        project_id=project.id,
        name="web",
        type="website",
        api_key="platform-key",
        is_active=True,
    )
    visitor = Visitor(
        id=uuid4(),
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id="visitor-open-id",
    )
    config = ProjectAIConfig(
        project_id=project.id,
        default_chat_provider_id=uuid4(),
        default_chat_model="deepseek-v4-flash",
    )
    orchestrator = MessageIntentOrchestrator(
        _Session(config, visitor),  # type: ignore[arg-type]
        ai_client=_LogisticsAIClient(),  # type: ignore[arg-type]
        workflow_client=_WorkflowClient(),  # type: ignore[arg-type]
        logistics_service=_FailingLogisticsService(failure),  # type: ignore[arg-type]
    )

    outcome = await orchestrator.analyze_text_message(
        project=project,
        platform=platform,
        visitor=visitor,
        source_message_id="user-message-failed-tracking",
        user_text="快递单号 SF1234567890",
    )

    assert outcome.routing_target == "clarify"
    assert outcome.handoff_result is None
    assert all(
        "private-logistics-response" not in str(record.__dict__)
        for record in caplog.records
    )
    assert all(
        "unexpected query:" not in str(record.__dict__)
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_ai_client_uses_registered_intent_route() -> None:
    client = AIServiceClient()
    client._make_request = AsyncMock(return_value=object())  # type: ignore[method-assign]
    client._handle_response = AsyncMock(  # type: ignore[method-assign]
        return_value={"intent": "logistics_query"}
    )

    await client.classify_intent(
        project_id=str(uuid4()),
        provider_id=str(uuid4()),
        model="deepseek-v4-flash",
        classification_input={"user_text": "我想查询物流"},
    )

    assert client._make_request.await_args.args[:2] == (
        "POST",
        "/api/v1/analysis/intent",
    )
    headers = client._make_request.await_args.kwargs["extra_headers"]
    assert headers["X-Internal-API-Key"]
    assert headers["X-Project-Id"]
