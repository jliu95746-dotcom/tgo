"""Append-only decisions on a frozen shadow sample's disputed reviews."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ShadowReviewAdjudication(Base):
    __tablename__ = "api_shadow_review_adjudications"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "request_id", name="uq_shadow_adjudication_request"
        ),
        ForeignKeyConstraint(
            ["batch_id", "draft_id"],
            [
                "api_shadow_review_batch_items.batch_id",
                "api_shadow_review_batch_items.draft_id",
            ],
            ondelete="RESTRICT",
            name="fk_shadow_adjudication_sample_member",
        ),
        CheckConstraint(
            "verdict IN ('acceptable', 'minor_issue', 'severe_error')",
            name="ck_shadow_adjudication_verdict",
        ),
        Index(
            "ix_shadow_adjudication_case_created",
            "project_id",
            "batch_id",
            "draft_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[UUID] = mapped_column(nullable=False)
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    visitor_id: Mapped[UUID] = mapped_column(nullable=False)
    adjudicator_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    review_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
