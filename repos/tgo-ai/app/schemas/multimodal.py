"""Strongly typed contracts for provider-neutral multimodal analysis."""

from enum import Enum
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MultimodalSchema(BaseModel):
    """Strict base schema for internal multimodal contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


class MediaType(str, Enum):
    """Media types supported by the first multimodal pipeline."""

    VOICE = "voice"
    IMAGE = "image"


class AnalysisCapability(str, Enum):
    """Provider capability used by an analysis stage."""

    ASR = "asr"
    OCR = "ocr"
    VLM = "vlm"


class AnalysisStageStatus(str, Enum):
    """Terminal status for one provider call."""

    COMPLETED = "completed"
    FAILED = "failed"


class MediaAnalysisStatus(str, Enum):
    """Aggregated status for all required analysis stages."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class AnalysisErrorCategory(str, Enum):
    """Stable categories suitable for retry and user-visible status logic."""

    TIMEOUT = "timeout"
    INVALID_MEDIA = "invalid_media"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_FAILURE = "provider_failure"


class SensitiveDataCategory(str, Enum):
    """Sensitive customer-data categories removed before downstream use."""

    PHONE_NUMBER = "phone_number"
    IDENTITY_NUMBER = "identity_number"
    PAYMENT_ACCOUNT = "payment_account"
    EMAIL = "email"
    ADDRESS = "address"


class MediaAnalysisRequest(MultimodalSchema):
    """Canonical media reference accepted by the multimodal service."""

    media_id: str = Field(min_length=1, max_length=128)
    media_type: MediaType
    media_uri: str = Field(min_length=1, max_length=2048)
    mime_type: str = Field(min_length=3, max_length=128)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_text: str | None = Field(
        default=None, min_length=1, max_length=4096
    )
    language: str | None = Field(default=None, min_length=2, max_length=32)

    @model_validator(mode="after")  # type: ignore[misc]
    def validate_media_mime_type(self) -> Self:
        """Reject a media type/MIME mismatch before contacting a provider."""
        expected_prefix = (
            "audio/" if self.media_type is MediaType.VOICE else "image/"
        )
        if not self.mime_type.lower().startswith(expected_prefix):
            raise ValueError(
                f"{self.media_type.value} media requires a "
                f"{expected_prefix} MIME type"
            )
        parsed_uri = urlsplit(self.media_uri)
        if (
            parsed_uri.scheme not in {"s3", "tgo-media"}
            or not parsed_uri.netloc
            or not parsed_uri.path
            or parsed_uri.username is not None
            or parsed_uri.password is not None
            or parsed_uri.query
            or parsed_uri.fragment
        ):
            raise ValueError(
                "media_uri must be an internal object reference without "
                "credentials"
            )
        return self


class ProviderMediaRequest(MultimodalSchema):
    """Validated provider input without channel-specific details."""

    media_id: str = Field(min_length=1, max_length=128)
    media_uri: str = Field(min_length=1, max_length=2048)
    mime_type: str = Field(min_length=3, max_length=128)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ASRRequest(ProviderMediaRequest):
    """Automatic speech recognition request."""

    language: str | None = Field(default=None, min_length=2, max_length=32)


class OCRRequest(ProviderMediaRequest):
    """Optical character recognition request."""


class VLMRequest(ProviderMediaRequest):
    """Visual language model request."""


class ASROutput(MultimodalSchema):
    """Provider-neutral speech transcription output."""

    transcript: str = Field(min_length=1, max_length=65535)
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, allow_inf_nan=False
    )
    language: str | None = Field(default=None, min_length=2, max_length=32)
    model_version: str = Field(min_length=1, max_length=128)


class OCROutput(MultimodalSchema):
    """Provider-neutral text extraction output."""

    text: str = Field(min_length=1, max_length=65535)
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, allow_inf_nan=False
    )
    model_version: str = Field(min_length=1, max_length=128)


class VLMOutput(MultimodalSchema):
    """Provider-neutral visual summary output."""

    summary: str = Field(min_length=1, max_length=65535)
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, allow_inf_nan=False
    )
    model_version: str = Field(min_length=1, max_length=128)


class AnalysisStageError(MultimodalSchema):
    """Sanitized provider error returned to orchestration code."""

    category: AnalysisErrorCategory
    message: str = Field(min_length=1, max_length=512)
    retryable: bool


class AnalysisStageResult(MultimodalSchema):
    """Result of one ASR, OCR, or VLM call."""

    capability: AnalysisCapability
    status: AnalysisStageStatus
    provider_name: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    text: str | None = Field(default=None, min_length=1, max_length=65535)
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, allow_inf_nan=False
    )
    model_version: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    error: AnalysisStageError | None = None
    text_is_untrusted: bool = False
    sensitive_data_categories: tuple[SensitiveDataCategory, ...] = ()

    @model_validator(mode="after")  # type: ignore[misc]
    def validate_terminal_state(self) -> Self:
        """Ensure successful and failed stage payloads cannot be confused."""
        if self.status is AnalysisStageStatus.COMPLETED:
            if (
                self.provider_name is None
                or self.text is None
                or self.error is not None
                or not self.text_is_untrusted
            ):
                raise ValueError(
                    "completed stage requires provider and text without error"
                )
        elif self.error is None or self.text is not None:
            raise ValueError(
                "failed stage requires an error and cannot contain text"
            )
        return self


class MediaAnalysisResult(MultimodalSchema):
    """Unified result consumed by later intent and response orchestration."""

    media_id: str = Field(min_length=1, max_length=128)
    media_type: MediaType
    status: MediaAnalysisStatus
    normalized_text: str | None = Field(
        default=None, min_length=1, max_length=131072
    )
    stages: tuple[AnalysisStageResult, ...] = Field(min_length=1)
    can_continue: bool = Field(
        description="True only when every required provider stage completed"
    )
    requires_handoff: bool
    fallback_message: str | None = Field(
        default=None, min_length=1, max_length=512
    )
    normalized_text_is_untrusted: bool
    sensitive_data_categories: tuple[SensitiveDataCategory, ...] = ()

    @model_validator(mode="after")  # type: ignore[misc]
    def validate_fail_closed_state(self) -> Self:
        """Align aggregate status with stages and fallback policy."""
        completed_count = sum(
            stage.status is AnalysisStageStatus.COMPLETED
            for stage in self.stages
        )
        if completed_count == len(self.stages):
            derived_status = MediaAnalysisStatus.COMPLETED
        elif completed_count == 0:
            derived_status = MediaAnalysisStatus.FAILED
        else:
            derived_status = MediaAnalysisStatus.PARTIAL

        if self.status is not derived_status:
            raise ValueError(
                "media analysis status does not match stage results"
            )
        expected_capabilities = (
            (AnalysisCapability.ASR,)
            if self.media_type is MediaType.VOICE
            else (AnalysisCapability.OCR, AnalysisCapability.VLM)
        )
        if (
            tuple(stage.capability for stage in self.stages)
            != expected_capabilities
        ):
            raise ValueError(
                "media analysis stages do not match the media type"
            )
        if (
            self.status is MediaAnalysisStatus.COMPLETED
            and self.normalized_text is None
        ):
            raise ValueError(
                "completed media analysis requires normalized text"
            )
        if (
            self.normalized_text is not None
        ) is not self.normalized_text_is_untrusted:
            raise ValueError(
                "normalized text must be marked as untrusted customer content"
            )
        if self.status is MediaAnalysisStatus.COMPLETED:
            if not self.can_continue or self.requires_handoff:
                raise ValueError(
                    "completed analysis must be allowed to continue"
                )
            if self.fallback_message is not None:
                raise ValueError(
                    "completed analysis cannot have a fallback message"
                )
        elif self.can_continue or not self.requires_handoff:
            raise ValueError(
                "incomplete analysis must fail closed and require handoff"
            )
        elif self.fallback_message is None:
            raise ValueError(
                "incomplete analysis requires a visible fallback message"
            )
        return self


class ProviderSelection(MultimodalSchema):
    """Configured provider names for each independent capability."""

    asr: str | None = Field(default=None, min_length=1, max_length=128)
    ocr: str | None = Field(default=None, min_length=1, max_length=128)
    vlm: str | None = Field(default=None, min_length=1, max_length=128)
