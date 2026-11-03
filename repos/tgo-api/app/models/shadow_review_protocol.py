"""One pre-registered official G4 sampling protocol per project."""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ShadowReviewProtocol(Base):
    __tablename__ = "api_shadow_review_protocols"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "request_id", name="uq_shadow_protocol_project_request"
        ),
        UniqueConstraint("batch_id", name="uq_shadow_protocol_batch"),
        Index(
            "uq_shadow_protocol_active_project",
            "project_id",
            unique=True,
            postgresql_where=text("finalized_at IS NULL"),
            sqlite_where=text("finalized_at IS NULL"),
        ),
        Index("ix_shadow_protocol_project_created", "project_id", "created_at"),
        CheckConstraint("window_start < window_end", name="ck_shadow_protocol_window"),
        CheckConstraint(
            "sample_size BETWEEN 1 AND 500", name="ck_shadow_protocol_sample_size"
        ),
        CheckConstraint(
            "finalized_at IS NULL OR finalized_at >= created_at",
            name="ck_shadow_protocol_finalized",
        ),
        CheckConstraint(
            "(finalized_at IS NULL AND batch_id IS NULL AND "
            "population_count IS NULL AND population_sha256 IS NULL) OR "
            "(finalized_at IS NOT NULL AND population_count >= 0 AND "
            "population_sha256 IS NOT NULL AND "
            "((population_count = 0 AND batch_id IS NULL) OR "
            "(population_count > 0 AND batch_id IS NOT NULL)))",
            name="ck_shadow_protocol_outcome",
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
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    random_seed: Mapped[str] = mapped_column(String(32), nullable=False)
    population_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    population_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_shadow_review_batches.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ShadowReviewProtocolCandidate(Base):
    """Every eligible draft ID, including cases not picked for human review."""

    __tablename__ = "api_shadow_review_protocol_candidates"

    protocol_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_shadow_review_protocols.id", ondelete="CASCADE"),
        primary_key=True,
    )
    draft_id: Mapped[UUID] = mapped_column(primary_key=True)
    visitor_id: Mapped[UUID] = mapped_column(nullable=False)
