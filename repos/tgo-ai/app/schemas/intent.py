"""Strict intent-classification contracts for taxonomy version 1."""

from __future__ import annotations

import unicodedata
from enum import Enum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.multimodal import SensitiveDataCategory


class StrictIntentSchema(BaseModel):
    """Base contract that rejects coercion and undeclared model fields."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class IntentName(str, Enum):
    """Stable English identifiers for the version 1 intent taxonomy."""

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


INTENT_TAXONOMY_V1: tuple[str, ...] = tuple(
    intent.value for intent in IntentName
)


class RiskLevel(str, Enum):
    """Risk assigned to acting on an intent classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IntentRoute(str, Enum):
    """Allowed routes; no route authorizes a state-changing tool."""

    AUTO_REPLY = "auto_reply"
    READ_ONLY_TOOL = "read_only_tool"
    CLARIFY = "clarify"
    HUMAN_HANDOFF = "human_handoff"


class RoutingReason(str, Enum):
    """Machine-readable explanation for the enforced route."""

    HIGH_CONFIDENCE_FAQ = "high_confidence_faq"
    HIGH_CONFIDENCE_READ_ONLY = "high_confidence_read_only"
    MEDIUM_CONFIDENCE = "medium_confidence"
    LOW_CONFIDENCE = "low_confidence"
    HIGH_RISK = "high_risk"
    SENSITIVE_INTENT = "sensitive_intent"
    MEDIA_PROCESSING_FAILED = "media_processing_failed"
    UNTRUSTED_MEDIA_CONFIRMATION = "untrusted_media_confirmation"
    SENSITIVE_DATA_DETECTED = "sensitive_data_detected"
    MEDIUM_RISK = "medium_risk"
    AUTOMATION_DISABLED = "automation_disabled"
    UNKNOWN_CLARIFICATION = "unknown_clarification"
    REPEATED_UNKNOWN = "repeated_unknown"
    RULE_MATCH = "rule_match"
    CLASSIFICATION_FAILED = "classification_failed"


class ClassificationSource(str, Enum):
    """Source of the classification decision for evaluation and audit."""

    MODEL = "model"
    RULE = "rule"
    FAIL_CLOSED = "fail_closed"


class IntentEntities(StrictIntentSchema):
    """Whitelisted business entities extracted from untrusted customer text."""

    order_no: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    product_name: str | None = Field(
        default=None, min_length=1, max_length=256
    )
    sku: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    logistics_no: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    payment_reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    issue_summary: str | None = Field(
        default=None, min_length=1, max_length=500
    )

    @field_validator("product_name", "issue_summary")  # type: ignore[misc]
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(
            unicodedata.category(character) in {"Cc", "Cf"}
            for character in value
        ):
            raise ValueError("entity text cannot contain control characters")
        return value


class IntentModelOutput(StrictIntentSchema):
    """Exact JSON object required from a structured-output provider."""

    intent: IntentName
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    entities: IntentEntities
    risk_level: RiskLevel
    recommended_route: IntentRoute
    need_human: bool
    taxonomy_version: Literal["v1"]


class IntentRoutingContext(StrictIntentSchema):
    """Deterministic non-model signals used by the routing policy."""

    media_processing_failed: bool = False
    contains_untrusted_media_text: bool = False
    contains_sensitive_data: bool = False
    consecutive_unknown_count: int = Field(default=0, ge=0)


class IntentClassificationInput(StrictIntentSchema):
    """Structured, source-preserving customer content sent to
    classification.
    """

    user_text: str | None = Field(default=None, min_length=1, max_length=8192)
    asr_text: str | None = Field(default=None, min_length=1, max_length=65535)
    ocr_text: str | None = Field(default=None, min_length=1, max_length=65535)
    vlm_text: str | None = Field(default=None, min_length=1, max_length=65535)
    sensitive_data_categories: tuple[SensitiveDataCategory, ...] = ()
    consecutive_unknown_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")  # type: ignore[misc]
    def require_customer_content(self) -> Self:
        if not any(
            (self.user_text, self.asr_text, self.ocr_text, self.vlm_text)
        ):
            raise ValueError("classification input requires customer content")
        return self

    @property
    def contains_untrusted_media_text(self) -> bool:
        return any((self.asr_text, self.ocr_text, self.vlm_text))


class IntentClassificationResult(StrictIntentSchema):
    """Policy-enforced classification returned to downstream services."""

    intent: IntentName
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    entities: IntentEntities
    risk_level: RiskLevel
    recommended_route: IntentRoute
    need_human: bool
    taxonomy_version: Literal["v1"]
    routing_reason: RoutingReason
    classification_source: ClassificationSource
