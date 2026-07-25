"""End-to-end contract for intent analysis orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

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


@pytest.mark.asyncio
async def test_safe_short_message_skips_redundant_intent_model_call() -> None:
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
    ai_client = _UnexpectedAIClient()
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
        user_text="请简单介绍一下你们的功能",
    )

    assert ai_client.called is False
    assert outcome.intent_result.intent == "product_inquiry"
    assert outcome.intent_result.classification_source == "rule"
    assert outcome.routing_target == "auto_reply"
    assert workflow_client.last_request is not None


@pytest.mark.parametrize(
    "message",
    (
        "请帮我查询订单",
        "我想转人工客服",
        "这个商品坏了，怎么售后",
        "账号被盗了",
        "请帮我退！款",
    ),
)
def test_special_routing_messages_keep_semantic_classification(message: str) -> None:
    assert MessageIntentOrchestrator._can_use_safe_auto_reply(message) is False


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
    assert outcome.routing_target == "read_only_tool"
    assert outcome.routing_reason == "read_only_query_completed"
    assert outcome.tool_context is not None
    assert "SF****5678" in outcome.tool_context
    assert session.intent is outcome.intent_result
    assert plugin_client.last_request is not None
    request_context = plugin_client.last_request["context"]
    assert isinstance(request_context, dict)
    assert request_context["external_customer_id"] == "visitor-open-id"


@pytest.mark.asyncio
async def test_business_query_prefers_explicit_business_customer_id() -> None:
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

    assert plugin_client.last_request is not None
    context = plugin_client.last_request["context"]
    assert isinstance(context, dict)
    assert context["external_customer_id"] == "member-7788"


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
