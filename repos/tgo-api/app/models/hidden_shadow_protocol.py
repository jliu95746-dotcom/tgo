"""Pre-registered independent G4 sample and its immutable case manifest."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
    UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HiddenShadowProtocol(Base):
    __tablename__ = "api_hidden_shadow_protocols"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "request_id", name="uq_hidden_protocol_request",
        ),
        Index(
            "uq_hidden_protocol_active_project", "project_id", unique=True,
            postgresql_where=text(
                "finalized_at IS NULL AND cancelled_at IS NULL"
            ),
            sqlite_where=text(
                "finalized_at IS NULL AND cancelled_at IS NULL"
            ),
        ),
        CheckConstraint(
            "window_start < window_end", name="ck_hidden_protocol_window",
        ),
        CheckConstraint(
            "sample_size BETWEEN 1 AND 500", name="ck_hidden_protocol_size",
        ),
        CheckConstraint(
            "(finalized_at IS NULL AND population_count IS NULL AND "
            "sample_count IS NULL AND population_sha256 IS NULL AND "
            "manifest_sha256 IS NULL) OR "
            "(finalized_at IS NOT NULL AND population_count >= 0 AND "
            "sample_count >= 0 AND sample_count <= population_count AND "
            "sample_count <= sample_size AND "
            "population_sha256 IS NOT NULL AND manifest_sha256 IS NOT NULL)",
            name="ck_hidden_protocol_outcome",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False,
    )
    creator_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(40), nullable=False)
    random_seed: Mapped[str] = mapped_column(String(32), nullable=False)
    seed_sha256: Mapped[str | None] = mapped_column(String(64))
    population_count: Mapped[int | None] = mapped_column(Integer)
    sample_count: Mapped[int | None] = mapped_column(Integer)
    population_sha256: Mapped[str | None] = mapped_column(String(64))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    cancelled_by: Mapped[UUID | None] = mapped_column()


class HiddenShadowProtocolCase(Base):
    __tablename__ = "api_hidden_shadow_protocol_cases"
    __table_args__ = (
        UniqueConstraint(
            "protocol_id", "staff_delivery_id",
            name="uq_hidden_protocol_delivery",
        ),
        UniqueConstraint(
            "protocol_id", "selected_position",
            name="uq_hidden_protocol_position",
        ),
        CheckConstraint(
            "selected_position IS NULL OR selected_position > 0",
            name="ck_hidden_protocol_position",
        ),
    )

    protocol_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_hidden_shadow_protocols.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_hidden_shadow_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    visitor_id: Mapped[UUID] = mapped_column(nullable=False)
    staff_delivery_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_staff_message_deliveries.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_message_id: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    source_content_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    staff_reply_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    staff_reply_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    selected_position: Mapped[int | None] = mapped_column(Integer)
