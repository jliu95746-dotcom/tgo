"""Global customer-intelligence projection from trusted message analysis."""

from __future__ import annotations

from uuid import uuid4

from app.models import MessageIntentResult, VisitorAIInsight, VisitorAIProfile
from app.services.customer_intelligence_service import CustomerIntelligenceService


class _Query:
    def __init__(self, session: "_Session", model: type[object]) -> None:
        self._session = session
        self._model = model

    def filter(self, *_criteria: object) -> "_Query":
        return self

    def first(self) -> object | None:
        if self._model is VisitorAIInsight:
            return self._session.insight
        if self._model is VisitorAIProfile:
            return self._session.profile
        raise AssertionError(f"unexpected query model: {self._model}")


class _Session:
    def __init__(self) -> None:
        self.insight: VisitorAIInsight | None = None
        self.profile: VisitorAIProfile | None = None
        self.commit_count = 0

    def query(self, model: type[object]) -> _Query:
        return _Query(self, model)

    def add(self, value: object) -> None:
        if isinstance(value, VisitorAIInsight):
            self.insight = value
        elif isinstance(value, VisitorAIProfile):
            self.profile = value
        else:
            raise AssertionError(f"unexpected add: {value}")

    def commit(self) -> None:
        self.commit_count += 1


def _intent_result(
    *,
    intent: str,
    classification_source: str = "model",
    routing_reason: str = "high_confidence_faq",
) -> MessageIntentResult:
    return MessageIntentResult(
        id=uuid4(),
        project_id=uuid4(),
        platform_id=uuid4(),
        visitor_id=uuid4(),
        source_message_id="message-1",
        intent=intent,
        confidence=0.96,
        entities={},
        risk_level="low",
        recommended_route="auto_reply",
        need_human=False,
        taxonomy_version="v1",
        routing_reason=routing_reason,
        classification_source=classification_source,
        classifier_version="classifier-v1",
        policy_version="policy-v1",
        input_fingerprint="fingerprint",
    )


def test_model_intent_updates_latest_insight_and_materializes_profile() -> None:
    session = _Session()
    result = _intent_result(intent="sales_lead")

    updated = CustomerIntelligenceService(session).record_intent(result)  # type: ignore[arg-type]

    assert updated is True
    assert session.insight is not None
    assert session.insight.intent == "sales_lead"
    assert session.insight.insight_metadata["source_message_id"] == "message-1"
    assert session.profile is not None
    assert session.profile.persona_tags == ["high_intent"]
    assert session.profile.summary is not None
    assert session.profile.summary["latest_intent"] == "sales_lead"
    assert session.commit_count == 1


def test_fast_path_rule_does_not_pollute_customer_profile() -> None:
    session = _Session()
    result = _intent_result(
        intent="product_inquiry",
        classification_source="rule",
        routing_reason="high_confidence_faq",
    )

    updated = CustomerIntelligenceService(session).record_intent(result)  # type: ignore[arg-type]

    assert updated is False
    assert session.insight is None
    assert session.profile is None
    assert session.commit_count == 0
