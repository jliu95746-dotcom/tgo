"""Strict contracts for persisted media analysis and intent results."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class AnalysisSchema(BaseModel):
    """Reject undeclared fields at the cross-service persistence boundary."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class MediaType(str, Enum):
    """Media types supported by the first processing pipeline."""

    VOICE = "voice"
    IMAGE = "image"


class MediaAnalysisStatus(str, Enum):
    """Aggregate media analysis state."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class AnalysisCapability(str, Enum):
    """Provider capability represented by an analysis stage."""

    ASR = "asr"
    OCR = "ocr"
    VLM = "vlm"


class AnalysisStageStatus(str, Enum):
    """Terminal stage state."""

    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisErrorCategory(str, Enum):
    """Sanitized failure categories accepted from analysis workers."""

    TIMEOUT = "timeout"
    INVALID_MEDIA = "invalid_media"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_FAILURE = "provider_failure"


class SensitiveDataCategory(str, Enum):
    """Sensitive customer-data categories removed before persistence."""

    PHONE_NUMBER = "phone_number"
    IDENTITY_NUMBER = "identity_number"
    PAYMENT_ACCOUNT = "payment_account"
    EMAIL = "email"
    ADDRESS = "address"


class AnalysisStageError(AnalysisSchema):
    """Stable error safe to persist and display."""

    category: AnalysisErrorCategory
    message: str = Field(min_length=1, max_length=512)
    retryable: bool


class AnalysisStageResult(AnalysisSchema):
    """One ASR, OCR, or VLM terminal result."""

    capability: AnalysisCapability
    status: AnalysisStageStatus
    provider_name: str | None = Field(default=None, min_length=1, max_length=128)
    text: str | None = Field(default=None, min_length=1, max_length=65535)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    model_version: str | None = Field(default=None, min_length=1, max_length=128)
    error: AnalysisStageError | None = None
    text_is_untrusted: bool = False
    sensitive_data_categories: tuple[SensitiveDataCategory, ...] = ()

    @model_validator(mode="after")  # type: ignore[misc]
    def validate_terminal_state(self) -> Self:
        """Prevent a successful stage from also carrying an error."""
        if self.status is AnalysisStageStatus.COMPLETED:
            if (
                self.provider_name is None
                or self.text is None
                or self.model_version is None
                or self.error is not None
                or not self.text_is_untrusted
            ):
                raise ValueError(
                    "completed stage requires provider, text, and model version"
                )
        elif self.error is None or self.text is not None:
            raise ValueError("failed stage requires an error without text")
        return self


class MediaResultUpsertRequest(AnalysisSchema):
    """Final media analysis supplied by a trusted platform integration."""

    visitor_id: UUID
    source_media_record_id: UUID
    media_type: MediaType
    media_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(min_length=3, max_length=128)
    status: MediaAnalysisStatus
    normalized_text: str | None = Field(default=None, min_length=1, max_length=131072)
    normalized_text_is_untrusted: bool
    sensitive_data_categories: tuple[SensitiveDataCategory, ...] = ()
    transcript: str | None = Field(default=None, min_length=1, max_length=65535)
    ocr_text: str | None = Field(default=None, min_length=1, max_length=65535)
    vision_summary: str | None = Field(default=None, min_length=1, max_length=65535)
    stages: tuple[AnalysisStageResult, ...] = Field(min_length=1, max_length=3)
    can_continue: bool
    requires_handoff: bool
    fallback_message: str | None = Field(default=None, min_length=1, max_length=512)
    pipeline_version: str = Field(min_length=1, max_length=128)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")  # type: ignore[misc]
    def validate_media_result(self) -> Self:
        """Keep status, stages, and fail-closed routing consistent."""
        expected_prefix = "audio/" if self.media_type is MediaType.VOICE else "image/"
        if not self.mime_type.lower().startswith(expected_prefix):
            raise ValueError(
                f"{self.media_type.value} requires a {expected_prefix} MIME type"
            )

        completed_count = sum(
            stage.status is AnalysisStageStatus.COMPLETED for stage in self.stages
        )
        if completed_count == len(self.stages):
            derived_status = MediaAnalysisStatus.COMPLETED
        elif completed_count == 0:
            derived_status = MediaAnalysisStatus.FAILED
        else:
            derived_status = MediaAnalysisStatus.PARTIAL
        if self.status is not derived_status:
            raise ValueError("status does not match stage results")

        capabilities = tuple(stage.capability for stage in self.stages)
        required_capabilities = (
            (AnalysisCapability.ASR,)
            if self.media_type is MediaType.VOICE
            else (AnalysisCapability.OCR, AnalysisCapability.VLM)
        )
        if capabilities != required_capabilities:
            raise ValueError("analysis stages do not match the media type")

        if (self.normalized_text is not None) is not self.normalized_text_is_untrusted:
            raise ValueError(
                "normalized text must be marked as untrusted customer content"
            )

        if self.status is MediaAnalysisStatus.COMPLETED:
            if (
                not self.can_continue
                or self.requires_handoff
                or self.fallback_message is not None
                or self.normalized_text is None
            ):
                raise ValueError("completed analysis must be safe to continue")
            if self.media_type is MediaType.VOICE and self.transcript is None:
                raise ValueError("completed voice analysis requires transcript")
            if self.media_type is MediaType.IMAGE and (
                self.ocr_text is None or self.vision_summary is None
            ):
                raise ValueError(
                    "completed image analysis requires OCR and vision summary"
                )
        elif (
            self.can_continue
            or not self.requires_handoff
            or self.fallback_message is None
        ):
            raise ValueError("incomplete analysis must fail closed")
        return self


class IntentName(str, Enum):
    """Version 1 customer-service intent taxonomy."""

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
    """Risk assigned by the deterministic routing policy."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IntentRoute(str, Enum):
    """Permitted downstream route."""

    AUTO_REPLY = "auto_reply"
    READ_ONLY_TOOL = "read_only_tool"
    CLARIFY = "clarify"
    HUMAN_HANDOFF = "human_handoff"


class RoutingReason(str, Enum):
    """Machine-readable reason for the enforced route."""

    HIGH_CONFIDENCE_FAQ = "high_confidence_faq"
    HIGH_CONFIDENCE_READ_ONLY = "high_confidence_read_only"
    MEDIUM_CONFIDENCE = "medium_confidence"
    LOW_CONFIDENCE = "low_confidence"
    HIGH_RISK = "high_risk"
    SENSITIVE_INTENT = "sensitive_intent"
    MEDIA_PROCESSING_FAILED = "media_processing_failed"
    REPEATED_UNKNOWN = "repeated_unknown"
    RULE_MATCH = "rule_match"
    CLASSIFICATION_FAILED = "classification_failed"
    SENSITIVE_DATA_DETECTED = "sensitive_data_detected"
    UNTRUSTED_MEDIA_CONFIRMATION = "untrusted_media_confirmation"
    MEDIUM_RISK = "medium_risk"
    AUTOMATION_DISABLED = "automation_disabled"
    UNKNOWN_CLARIFICATION = "unknown_clarification"


class ClassificationSource(str, Enum):
    """Source of the classification decision for evaluation and audit."""

    MODEL = "model"
    RULE = "rule"
    FAIL_CLOSED = "fail_closed"


class IntentEntities(AnalysisSchema):
    """Whitelisted entities; arbitrary model output is rejected."""

    order_no: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    product_name: str | None = Field(default=None, min_length=1, max_length=256)
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
    issue_summary: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("product_name", "issue_summary")  # type: ignore[misc]
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(
            unicodedata.category(character) in {"Cc", "Cf"} for character in value
        ):
            raise ValueError("entity text cannot contain control characters")
        return value


class IntentResultUpsertRequest(AnalysisSchema):
    """Final policy-enforced intent result for persistence."""

    visitor_id: UUID
    media_analysis_result_id: UUID | None = None
    intent: IntentName
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    entities: IntentEntities
    risk_level: RiskLevel
    recommended_route: IntentRoute
    need_human: bool
    taxonomy_version: Literal["v1"]
    routing_reason: RoutingReason
    classification_source: ClassificationSource
    classifier_version: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")  # type: ignore[misc]
    def validate_handoff_state(self) -> Self:
        """A handoff flag and handoff route must always agree."""
        is_handoff = self.recommended_route is IntentRoute.HUMAN_HANDOFF
        if self.need_human != is_handoff:
            raise ValueError("need_human must match the human_handoff route")
        return self


class MediaResultResponse(MediaResultUpsertRequest):
    """Persisted media result with server-derived ownership and identity."""

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    id: UUID
    project_id: UUID
    platform_id: UUID
    source_message_id: str
    input_fingerprint: str
    created_at: datetime
    updated_at: datetime


class IntentResultResponse(IntentResultUpsertRequest):
    """Persisted intent result with server-derived ownership and identity."""

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    id: UUID
    project_id: UUID
    platform_id: UUID
    source_message_id: str
    input_fingerprint: str
    created_at: datetime
    updated_at: datetime


class CombinedMessageAnalysisResponse(AnalysisSchema):
    """Media and intent projections for one source message."""

    source_message_id: str
    media: MediaResultResponse | None = None
    intent: IntentResultResponse | None = None


class StaffMessageAnalysisLookup(AnalysisSchema):
    """One employee-console lookup tied to a visitor conversation."""

    channel_id: str = Field(
        min_length=40,
        max_length=40,
        pattern=(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}-vtr$"
        ),
    )
    source_message_id: str = Field(min_length=1, max_length=255)


class StaffMessageAnalysisBatchRequest(AnalysisSchema):
    """Bounded, duplicate-free employee-console analysis lookup batch."""

    messages: tuple[StaffMessageAnalysisLookup, ...] = Field(
        min_length=1,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_unique_messages(self) -> Self:
        keys = {
            (message.channel_id, message.source_message_id)
            for message in self.messages
        }
        if len(keys) != len(self.messages):
            raise ValueError("messages must not contain duplicate lookup keys")
        return self


class StaffMessageAnalysisResponse(CombinedMessageAnalysisResponse):
    """Available analysis projection for one employee-visible message."""

    channel_id: str


class StaffMessageAnalysisBatchResponse(AnalysisSchema):
    """Available projections; missing analyses are intentionally omitted."""

    items: tuple[StaffMessageAnalysisResponse, ...]
