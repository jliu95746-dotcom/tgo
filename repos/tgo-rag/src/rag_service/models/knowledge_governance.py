"""Persistent governance metadata for files and FAQ knowledge sources."""

from datetime import datetime
from typing import Optional
from uuid import UUID as PyUUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin


class KnowledgeGovernanceRecord(
    Base,
    UUIDMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    """Review and validity metadata gating automatic-answer retrieval."""

    __tablename__ = "rag_knowledge_governance"

    project_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    file_id: Mapped[Optional[PyUUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rag_files.id", ondelete="CASCADE"),
        nullable=True,
    )
    qa_pair_id: Mapped[Optional[PyUUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rag_qa_pairs.id", ondelete="CASCADE"),
        nullable=True,
    )
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    product_line: Mapped[str] = mapped_column(String(128), nullable=False)
    channels: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    document_version: Mapped[str] = mapped_column(String(64), nullable=False)
    allow_automatic_reply: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    review_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="draft",
        server_default="draft",
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    source_origin: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="internal",
        server_default="internal",
    )

    __table_args__ = (
        UniqueConstraint(
            "file_id",
            name="uq_rag_knowledge_governance_file_id",
        ),
        UniqueConstraint(
            "qa_pair_id",
            name="uq_rag_knowledge_governance_qa_pair_id",
        ),
        CheckConstraint(
            "num_nonnulls(file_id, qa_pair_id) = 1",
            name="ck_rag_knowledge_governance_exactly_one_source",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_at",
            name="ck_rag_knowledge_governance_valid_window",
        ),
        CheckConstraint(
            "cardinality(channels) BETWEEN 1 AND 16 "
            "AND channels <@ ARRAY["
            "'wecom_kf', 'web', 'app', 'phone', 'internal'"
            "]::varchar[]",
            name="ck_rag_knowledge_governance_channels",
        ),
        CheckConstraint(
            "btrim(product_line) <> '' AND btrim(owner) <> '' "
            "AND btrim(document_version) <> ''",
            name="ck_rag_knowledge_governance_nonblank_metadata",
        ),
        CheckConstraint(
            "document_type IN ('product', 'after_sales', 'faq', 'sop')",
            name="ck_rag_knowledge_governance_document_type",
        ),
        CheckConstraint(
            "review_status IN "
            "('draft', 'pending_review', 'approved', 'rejected', 'revoked')",
            name="ck_rag_knowledge_governance_review_status",
        ),
        CheckConstraint(
            "source_origin IN ('internal', 'customer', 'website')",
            name="ck_rag_knowledge_governance_source_origin",
        ),
        CheckConstraint(
            "review_status NOT IN ('approved', 'rejected', 'revoked') "
            "OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_rag_knowledge_governance_review_audit",
        ),
        Index(
            "idx_rag_knowledge_governance_project_review",
            "project_id",
            "review_status",
        ),
        Index(
            "idx_rag_knowledge_governance_validity",
            "effective_at",
            "expires_at",
        ),
        Index(
            "idx_rag_knowledge_governance_channels",
            "channels",
            postgresql_using="gin",
        ),
        Index(
            "idx_rag_knowledge_governance_deleted_at",
            "deleted_at",
        ),
    )
