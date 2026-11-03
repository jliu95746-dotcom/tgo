"""Append-only human assessment of a staff-visible shadow draft."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ShadowDraftReview(Base):
    __tablename__ = "api_shadow_draft_reviews"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "reviewer_id",
            "request_id",
            name="uq_shadow_review_reviewer_request",
        ),
        CheckConstraint(
            "verdict IN ('acceptable', 'minor_issue', "
            "'severe_error', 'insufficient_evidence')",
            name="ck_shadow_review_verdict",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            "('wrong_fact', 'privacy', 'unsafe_action', "
            "'missing_handoff', 'other')",
            name="ck_shadow_review_reason",
        ),
        Index(
            "ix_shadow_review_project_draft_created",
            "project_id",
            "draft_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_assist_draft_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_staff.id", ondelete="SET NULL"),
        nullable=True,
    )
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
