"""Project trusted message analysis into the shared customer intelligence view."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import MessageIntentResult, VisitorAIInsight, VisitorAIProfile


INTENT_PERSONA_TAGS: dict[str, str] = {
    "product_inquiry": "product_interest",
    "pricing_promotion": "price_sensitive",
    "order_assistance": "order_support",
    "order_query": "order_tracking",
    "logistics_query": "logistics_tracking",
    "payment_issue": "payment_support",
    "after_sales_issue": "after_sales",
    "refund_return_inquiry": "after_sales",
    "complaint": "complaint_risk",
    "sales_lead": "high_intent",
    "human_handoff": "needs_human",
}


class CustomerIntelligenceService:
    """Maintain the latest deterministic insight and profile for one visitor."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def record_intent(self, result: MessageIntentResult) -> bool:
        """Project a trusted classification without storing raw customer text."""

        if result.classification_source == "fail_closed":
            return False
        if (
            result.classification_source == "rule" and
            result.routing_reason == "high_confidence_faq"
        ):
            # The latency fast path deliberately skips semantic classification,
            # so it must not be treated as a real customer profile signal.
            return False

        insight = (
            self._db.query(VisitorAIInsight)
            .filter(
                VisitorAIInsight.project_id == result.project_id,
                VisitorAIInsight.visitor_id == result.visitor_id,
            )
            .first()
        )
        if insight is None:
            insight = VisitorAIInsight(
                project_id=result.project_id,
                visitor_id=result.visitor_id,
            )
            self._db.add(insight)

        insight.intent = result.intent
        insight.insight_summary = f"Latest classified intent: {result.intent}"
        insight.insight_metadata = {
            **(insight.insight_metadata or {}),
            "source": "message_intent_analysis",
            "source_message_id": result.source_message_id,
            "classification_source": result.classification_source,
            "classifier_version": result.classifier_version,
            "policy_version": result.policy_version,
            "confidence": result.confidence,
            "risk_level": result.risk_level,
            "recommended_route": result.recommended_route,
        }

        profile = (
            self._db.query(VisitorAIProfile)
            .filter(
                VisitorAIProfile.project_id == result.project_id,
                VisitorAIProfile.visitor_id == result.visitor_id,
            )
            .first()
        )
        if profile is None:
            profile = VisitorAIProfile(
                project_id=result.project_id,
                visitor_id=result.visitor_id,
            )
            self._db.add(profile)

        persona_tags = set(profile.persona_tags or [])
        mapped_tag = INTENT_PERSONA_TAGS.get(result.intent)
        if mapped_tag is not None:
            persona_tags.add(mapped_tag)
        profile.persona_tags = sorted(persona_tags)
        profile.summary = {
            **(profile.summary or {}),
            "latest_intent": result.intent,
            "confidence": result.confidence,
            "risk_level": result.risk_level,
            "recommended_route": result.recommended_route,
            "source_message_id": result.source_message_id,
        }

        self._db.commit()
        return True
