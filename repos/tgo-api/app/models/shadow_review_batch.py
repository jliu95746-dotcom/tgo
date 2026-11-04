"""Immutable membership for a G4 shadow review sample."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ShadowReviewBatch(Base):
    __tablename__ = "api_shadow_review_batches"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "request_id",
            name="uq_shadow_review_batch_project_request",
        ),
        CheckConstraint(
            "window_start < window_end",
            name="ck_shadow_review_batch_window",
        ),
        CheckConstraint(
            "requested_size BETWEEN 1 AND 500 AND "
            "sample_count BETWEEN 1 AND requested_size AND "
            "population_count >= sample_count",
            name="ck_shadow_review_batch_counts",
        ),
        Index(
            "ix_shadow_review_batch_project_created",
            "project_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False
    )
    creator_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    random_seed: Mapped[str] = mapped_column(String(32), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    population_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    population_count: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ShadowReviewBatchItem(Base):
    __tablename__ = "api_shadow_review_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "draft_id", name="uq_shadow_review_batch_draft"),
        CheckConstraint("position > 0", name="ck_shadow_review_batch_position"),
    )

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_shadow_review_batches.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    visitor_id: Mapped[UUID] = mapped_column(nullable=False)
