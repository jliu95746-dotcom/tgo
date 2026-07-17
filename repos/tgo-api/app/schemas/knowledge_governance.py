"""Typed contracts for knowledge governance proxy endpoints."""

from datetime import datetime
from enum import Enum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.base import PaginationMetadata


class KnowledgeDocumentType(str, Enum):
    PRODUCT = "product"
    AFTER_SALES = "after_sales"
    FAQ = "faq"
    SOP = "sop"


class KnowledgeChannel(str, Enum):
    WECOM_KF = "wecom_kf"
    WEB = "web"
    APP = "app"
    PHONE = "phone"
    INTERNAL = "internal"


class KnowledgeReviewStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class KnowledgeSourceOrigin(str, Enum):
    INTERNAL = "internal"
    CUSTOMER = "customer"
    WEBSITE = "website"


def _timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


class KnowledgeGovernanceDraftRequest(BaseModel):
    """Editable metadata; review audit fields are server controlled."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

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
        if not _timezone_aware(self.effective_at):
            raise ValueError("effective_at must be timezone-aware")
        if self.expires_at is not None:
            if not _timezone_aware(self.expires_at):
                raise ValueError("expires_at must be timezone-aware")
            if self.expires_at <= self.effective_at:
                raise ValueError("expires_at must be later than effective_at")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("channels must not contain duplicates")
        return self


class KnowledgeGovernanceBackfillRequest(BaseModel):
    """Backfill input that intentionally cannot approve or enable old knowledge."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    collection_id: UUID
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
    source_origin: KnowledgeSourceOrigin = KnowledgeSourceOrigin.INTERNAL
    dry_run: bool = True

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if not _timezone_aware(self.effective_at):
            raise ValueError("effective_at must be timezone-aware")
        if self.expires_at is not None:
            if not _timezone_aware(self.expires_at):
                raise ValueError("expires_at must be timezone-aware")
            if self.expires_at <= self.effective_at:
                raise ValueError("expires_at must be later than effective_at")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("channels must not contain duplicates")
        return self


class KnowledgeGovernanceReviewRequest(BaseModel):
    """Review target; actor and timestamp are injected from authentication."""

    model_config = ConfigDict(extra="forbid")

    status: KnowledgeReviewStatus

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status not in {
            KnowledgeReviewStatus.APPROVED,
            KnowledgeReviewStatus.REJECTED,
            KnowledgeReviewStatus.REVOKED,
        }:
            raise ValueError("review status must approve, reject, or revoke")
        return self


class KnowledgeGovernanceRecordResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    data: tuple[KnowledgeGovernanceRecordResponse, ...]
    pagination: PaginationMetadata


class KnowledgeGovernanceBackfillResponse(BaseModel):
    scanned_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    created_count: int = Field(ge=0)
    dry_run: bool
