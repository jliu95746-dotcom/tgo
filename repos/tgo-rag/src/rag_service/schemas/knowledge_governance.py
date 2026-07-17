"""Strongly typed contracts for customer-service knowledge governance."""

from datetime import datetime
from enum import Enum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from .common import PaginationMetadata


class KnowledgeDocumentType(str, Enum):
    """Supported first-stage customer-service knowledge categories."""

    PRODUCT = "product"
    AFTER_SALES = "after_sales"
    FAQ = "faq"
    SOP = "sop"


class KnowledgeChannel(str, Enum):
    """Channels to which a knowledge record may be applied."""

    WECOM_KF = "wecom_kf"
    WEB = "web"
    APP = "app"
    PHONE = "phone"
    INTERNAL = "internal"


class KnowledgeReviewStatus(str, Enum):
    """Review lifecycle for production knowledge."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class KnowledgeSourceOrigin(str, Enum):
    """Origin controls how retrieved content is treated by downstream prompts."""

    INTERNAL = "internal"
    CUSTOMER = "customer"
    WEBSITE = "website"


class AutomaticAnswerEligibilityReason(str, Enum):
    """Machine-readable reason returned by the fail-closed policy."""

    ELIGIBLE = "eligible"
    DELETED = "deleted"
    NOT_APPROVED = "not_approved"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    EXPIRED = "expired"
    AUTOMATIC_REPLY_DISABLED = "automatic_reply_disabled"
    CUSTOMER_CONTENT = "customer_content"
    CHANNEL_NOT_ALLOWED = "channel_not_allowed"
    INVALID_TIME_CONTEXT = "invalid_time_context"


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


class KnowledgeGovernanceInput(BaseModel):
    """Governance metadata attached to exactly one file or FAQ record."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    file_id: UUID | None = None
    qa_pair_id: UUID | None = None
    document_type: KnowledgeDocumentType
    product_line: str = Field(min_length=1, max_length=128)
    channels: tuple[KnowledgeChannel, ...] = Field(min_length=1, max_length=16)
    effective_at: datetime
    expires_at: datetime | None = None
    owner: str = Field(min_length=1, max_length=255)
    document_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    allow_automatic_reply: bool = False
    review_status: KnowledgeReviewStatus = KnowledgeReviewStatus.DRAFT
    reviewed_by: str | None = Field(default=None, min_length=1, max_length=255)
    reviewed_at: datetime | None = None
    source_origin: KnowledgeSourceOrigin = KnowledgeSourceOrigin.INTERNAL

    @computed_field  # type: ignore[misc]
    @property
    def content_is_untrusted(self) -> bool:
        """Customer and web content must retain an untrusted-input marker."""
        return self.source_origin in {
            KnowledgeSourceOrigin.CUSTOMER,
            KnowledgeSourceOrigin.WEBSITE,
        }

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        source_count = int(self.file_id is not None) + int(self.qa_pair_id is not None)
        if source_count != 1:
            raise ValueError("exactly one of file_id or qa_pair_id is required")

        if not _is_timezone_aware(self.effective_at):
            raise ValueError("effective_at must be timezone-aware")
        if self.expires_at is not None:
            if not _is_timezone_aware(self.expires_at):
                raise ValueError("expires_at must be timezone-aware")
            if self.expires_at <= self.effective_at:
                raise ValueError("expires_at must be later than effective_at")
        if self.reviewed_at is not None and not _is_timezone_aware(self.reviewed_at):
            raise ValueError("reviewed_at must be timezone-aware")

        if len(set(self.channels)) != len(self.channels):
            raise ValueError("channels must not contain duplicates")

        audited_statuses = {
            KnowledgeReviewStatus.APPROVED,
            KnowledgeReviewStatus.REJECTED,
            KnowledgeReviewStatus.REVOKED,
        }
        if self.review_status in audited_statuses and (
            self.reviewed_by is None or self.reviewed_at is None
        ):
            raise ValueError(
                "reviewed_by and reviewed_at are required for an audited review status"
            )
        return self


class KnowledgeGovernanceDraftRequest(BaseModel):
    """Editable governance metadata for a file-backed knowledge source."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    document_type: KnowledgeDocumentType
    product_line: str = Field(min_length=1, max_length=128)
    channels: tuple[KnowledgeChannel, ...] = Field(min_length=1, max_length=16)
    effective_at: datetime
    expires_at: datetime | None = None
    owner: str = Field(min_length=1, max_length=255)
    document_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    allow_automatic_reply: bool = False
    source_origin: KnowledgeSourceOrigin = KnowledgeSourceOrigin.INTERNAL

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if not _is_timezone_aware(self.effective_at):
            raise ValueError("effective_at must be timezone-aware")
        if self.expires_at is not None:
            if not _is_timezone_aware(self.expires_at):
                raise ValueError("expires_at must be timezone-aware")
            if self.expires_at <= self.effective_at:
                raise ValueError("expires_at must be later than effective_at")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("channels must not contain duplicates")
        return self


class KnowledgeGovernanceBackfillRequest(KnowledgeGovernanceDraftRequest):
    """Safe defaults for attaching governance metadata to existing files."""

    collection_id: UUID
    dry_run: bool = True
    allow_automatic_reply: bool = Field(default=False, frozen=True)
    review_status: KnowledgeReviewStatus = Field(
        default=KnowledgeReviewStatus.DRAFT,
        frozen=True,
    )

    @model_validator(mode="after")
    def validate_fail_closed_defaults(self) -> Self:
        if self.allow_automatic_reply:
            raise ValueError("backfill cannot enable automatic replies")
        if self.review_status is not KnowledgeReviewStatus.DRAFT:
            raise ValueError("backfill records must start as draft")
        return self


class KnowledgeGovernanceRecordResponse(BaseModel):
    """Governance record enriched with the source name and collection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    project_id: UUID
    file_id: UUID | None
    qa_pair_id: UUID | None
    collection_id: UUID
    source_name: str
    document_type: KnowledgeDocumentType
    product_line: str
    channels: tuple[KnowledgeChannel, ...]
    effective_at: datetime
    expires_at: datetime | None
    owner: str
    document_version: str
    allow_automatic_reply: bool
    review_status: KnowledgeReviewStatus
    reviewed_by: str | None
    reviewed_at: datetime | None
    source_origin: KnowledgeSourceOrigin
    content_is_untrusted: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeGovernanceListResponse(BaseModel):
    """Paginated governance records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data: tuple[KnowledgeGovernanceRecordResponse, ...]
    pagination: PaginationMetadata


class KnowledgeGovernanceBackfillResponse(BaseModel):
    """Dry-run or execution summary for legacy file governance backfill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scanned_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    created_count: int = Field(ge=0)
    dry_run: bool


class KnowledgeReviewDecision(BaseModel):
    """Audited approval, rejection, or revocation decision."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    status: KnowledgeReviewStatus
    reviewer: str = Field(min_length=1, max_length=255)
    reviewed_at: datetime

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        allowed = {
            KnowledgeReviewStatus.APPROVED,
            KnowledgeReviewStatus.REJECTED,
            KnowledgeReviewStatus.REVOKED,
        }
        if self.status not in allowed:
            raise ValueError("review decision must approve, reject, or revoke")
        if not _is_timezone_aware(self.reviewed_at):
            raise ValueError("reviewed_at must be timezone-aware")
        return self


class AutomaticAnswerEligibility(BaseModel):
    """Fail-closed policy decision consumed by retrieval orchestration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    eligible: bool
    reason: AutomaticAnswerEligibilityReason
    content_is_untrusted: bool
