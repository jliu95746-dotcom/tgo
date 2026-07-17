"""Strict contracts for deterministic customer-service workflow routing.

The routing boundary accepts classification metadata only. Raw customer text,
OCR, ASR, VLM output, prompts, and tool instructions are deliberately excluded
so untrusted content cannot become workflow control instructions.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictRoutingSchema(BaseModel):
    """Base schema that rejects coercion and undeclared control fields."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class CustomerServiceIntent(str, Enum):
    """Version 1 intent identifiers shared at the service boundary."""

    PRODUCT_INQUIRY = "product_inquiry"
    PRICING_PROMOTION = "pricing_promotion"
    ORDER_ASSISTANCE = "order_assistance"
    ORDER_QUERY = "order_query"
    LOGISTICS_QUERY = "logistics_query"
    PAYMENT_ISSUE = "payment_issue"
    AFTER_SALES_ISSUE = "after_sales_issue"
    REFUND_RETURN_INQUIRY = "refund_return_inquiry"
    COMPLAINT = "complaint"
    SALES_LEAD = "sales_lead"
    HUMAN_HANDOFF = "human_handoff"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    """Risk attached to the upstream classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RecommendedRoute(str, Enum):
    """Upstream recommendation; the workflow policy still enforces it."""

    AUTO_REPLY = "auto_reply"
    READ_ONLY_TOOL = "read_only_tool"
    CLARIFY = "clarify"
    HUMAN_HANDOFF = "human_handoff"


class MediaProcessingStatus(str, Enum):
    """Whether media-derived information is safe to consider structurally."""

    NOT_APPLICABLE = "not_applicable"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CustomerContentSource(str, Enum):
    """Origins that always remain untrusted customer-controlled content."""

    USER_TEXT = "user_text"
    ASR = "asr"
    OCR = "ocr"
    VLM = "vlm"


class ContentTrustBoundary(str, Enum):
    """Explicit invariant for all current customer content sources."""

    UNTRUSTED = "untrusted_customer_content"


class CustomerServiceRoutingTarget(str, Enum):
    """Destinations selected by routing; selection is not execution."""

    HUMAN_HANDOFF = "human_handoff"
    CLARIFY = "clarify"
    RAG = "rag"
    READ_ONLY_TOOL = "read_only_tool"


class RoutingReason(str, Enum):
    """Stable audit reason for a routing decision."""

    MEDIA_PROCESSING_FAILED = "media_processing_failed"
    HIGH_RISK = "high_risk"
    HUMAN_REQUIRED = "human_required"
    UPSTREAM_HANDOFF = "upstream_handoff"
    LOW_CONFIDENCE = "low_confidence"
    MEDIUM_CONFIDENCE = "medium_confidence"
    CLARIFICATION_REQUIRED = "clarification_required"
    REVIEWED_KNOWLEDGE = "reviewed_knowledge"
    READ_ONLY_QUERY = "read_only_query"
    UNSUPPORTED_ROUTE = "unsupported_route"


class IntentRoutingSignal(StrictRoutingSchema):
    """Policy-relevant subset of structured intent classification output."""

    intent: CustomerServiceIntent
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    risk_level: RiskLevel
    recommended_route: RecommendedRoute
    need_human: bool
    taxonomy_version: Literal["v1"]


class CustomerServiceRoutingInput(StrictRoutingSchema):
    """Routing input without raw text, prompt, or executable instructions."""

    classification: IntentRoutingSignal
    media_status: MediaProcessingStatus
    content_sources: tuple[CustomerContentSource, ...] = Field(min_length=1)
    content_trust_boundary: Literal[
        ContentTrustBoundary.UNTRUSTED
    ] = ContentTrustBoundary.UNTRUSTED

    @model_validator(mode="after")
    def validate_media_source_boundary(self) -> Self:
        media_sources = {
            CustomerContentSource.ASR,
            CustomerContentSource.OCR,
            CustomerContentSource.VLM,
        }
        has_media_source = any(
            source in media_sources for source in self.content_sources
        )
        if (
            self.media_status is not MediaProcessingStatus.NOT_APPLICABLE
            and not has_media_source
        ):
            raise ValueError("media_status requires a media source")
        if (
            self.media_status is MediaProcessingStatus.NOT_APPLICABLE
            and has_media_source
        ):
            raise ValueError("media source requires processing status")
        return self


class CustomerServiceRoutingDecision(StrictRoutingSchema):
    """Side-effect-free route selection returned to the workflow layer."""

    target: CustomerServiceRoutingTarget
    reason: RoutingReason
    content_trust_boundary: Literal[
        ContentTrustBoundary.UNTRUSTED
    ] = ContentTrustBoundary.UNTRUSTED
    execute_tool: Literal[False] = False
