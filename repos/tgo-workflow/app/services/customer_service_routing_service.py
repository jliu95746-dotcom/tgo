"""Deterministic and side-effect-free customer-service routing policy."""

from __future__ import annotations

from app.schemas.customer_service_routing import (
    CustomerServiceIntent,
    CustomerServiceRoutingDecision,
    CustomerServiceRoutingInput,
    CustomerServiceRoutingTarget,
    MediaProcessingStatus,
    RecommendedRoute,
    RiskLevel,
    RoutingReason,
)


class CustomerServiceRoutingService:
    """Select a safe workflow destination without calling downstream systems."""

    LOW_CONFIDENCE_THRESHOLD = 0.60
    AUTO_ROUTE_CONFIDENCE_THRESHOLD = 0.85

    _FAQ_INTENTS = frozenset(
        {
            CustomerServiceIntent.PRODUCT_INQUIRY,
            CustomerServiceIntent.PRICING_PROMOTION,
            CustomerServiceIntent.AFTER_SALES_ISSUE,
            CustomerServiceIntent.REFUND_RETURN_INQUIRY,
        }
    )
    _READ_ONLY_QUERY_INTENTS = frozenset(
        {
            CustomerServiceIntent.ORDER_QUERY,
            CustomerServiceIntent.LOGISTICS_QUERY,
        }
    )

    @classmethod
    def route(
        cls,
        routing_input: CustomerServiceRoutingInput,
    ) -> CustomerServiceRoutingDecision:
        """Apply the route policy in fail-closed precedence order."""

        classification = routing_input.classification

        if routing_input.media_status is MediaProcessingStatus.FAILED:
            return cls._handoff(RoutingReason.MEDIA_PROCESSING_FAILED)

        if classification.risk_level is RiskLevel.HIGH:
            return cls._handoff(RoutingReason.HIGH_RISK)

        if classification.need_human:
            return cls._handoff(RoutingReason.HUMAN_REQUIRED)

        if classification.recommended_route is RecommendedRoute.HUMAN_HANDOFF:
            return cls._handoff(RoutingReason.UPSTREAM_HANDOFF)

        if classification.recommended_route is RecommendedRoute.CLARIFY:
            return cls._decision(
                CustomerServiceRoutingTarget.CLARIFY,
                RoutingReason.CLARIFICATION_REQUIRED,
            )

        if classification.confidence < cls.LOW_CONFIDENCE_THRESHOLD:
            return cls._handoff(RoutingReason.LOW_CONFIDENCE)

        if classification.confidence < cls.AUTO_ROUTE_CONFIDENCE_THRESHOLD:
            return cls._decision(
                CustomerServiceRoutingTarget.CLARIFY,
                RoutingReason.MEDIUM_CONFIDENCE,
            )

        if (
            classification.intent in cls._FAQ_INTENTS
            and classification.recommended_route is RecommendedRoute.AUTO_REPLY
        ):
            return cls._decision(
                CustomerServiceRoutingTarget.RAG,
                RoutingReason.REVIEWED_KNOWLEDGE,
            )

        if (
            classification.intent in cls._READ_ONLY_QUERY_INTENTS
            and classification.recommended_route is RecommendedRoute.READ_ONLY_TOOL
        ):
            return cls._decision(
                CustomerServiceRoutingTarget.READ_ONLY_TOOL,
                RoutingReason.READ_ONLY_QUERY,
            )

        return cls._handoff(RoutingReason.UNSUPPORTED_ROUTE)

    @staticmethod
    def _decision(
        target: CustomerServiceRoutingTarget,
        reason: RoutingReason,
    ) -> CustomerServiceRoutingDecision:
        return CustomerServiceRoutingDecision(target=target, reason=reason)

    @classmethod
    def _handoff(
        cls,
        reason: RoutingReason,
    ) -> CustomerServiceRoutingDecision:
        return cls._decision(CustomerServiceRoutingTarget.HUMAN_HANDOFF, reason)
