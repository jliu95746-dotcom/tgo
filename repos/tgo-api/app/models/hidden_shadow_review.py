"""Append-only reviews of a frozen, independent private AI candidate."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HiddenShadowCaseReview(Base):
    __tablename__ = "api_hidden_shadow_case_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["protocol_id", "candidate_id"],
            [
                "api_hidden_shadow_protocol_cases.protocol_id",
                "api_hidden_shadow_protocol_cases.candidate_id",
            ],
            ondelete="RESTRICT",
            name="fk_hidden_review_frozen_case",
        ),
        UniqueConstraint(
            "protocol_id", "candidate_id", "reviewer_id",
            name="uq_hidden_review_case_reviewer",
        ),
        UniqueConstraint(
            "project_id", "reviewer_id", "request_id",
            name="uq_hidden_review_request",
        ),
        CheckConstraint(
            "verdict IN ('acceptable', 'minor_issue', "
            "'severe_error', 'insufficient_evidence')",
            name="ck_hidden_review_verdict",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            "('wrong_fact', 'privacy', 'unsafe_action', "
            "'missing_handoff', 'other')",
            name="ck_hidden_review_reason",
        ),
        Index("ix_hidden_review_protocol", "protocol_id", "candidate_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False,
    )
    protocol_id: Mapped[UUID] = mapped_column(nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False)
    reviewer_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    source_message_seq: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    source_content_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
