"""Persisted multimodal analysis for one inbound platform message."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MediaAnalysisResult(Base):
    """Current analysis result for a media-bearing source message."""

    __tablename__ = "api_media_analysis_results"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_platforms.id", ondelete="CASCADE"),
        nullable=False,
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_media_record_id: Mapped[UUID] = mapped_column(nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    media_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_text_is_untrusted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sensitive_data_categories: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    vision_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    stages: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    can_continue: Mapped[bool] = mapped_column(Boolean, nullable=False)
    requires_handoff: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fallback_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pipeline_version: Mapped[str] = mapped_column(String(128), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "platform_id",
            "source_message_id",
            name="uq_media_analysis_source_message",
        ),
        UniqueConstraint(
            "project_id",
            "platform_id",
            "source_media_record_id",
            name="uq_media_analysis_source_media",
        ),
        CheckConstraint(
            "media_type IN ('voice', 'image')",
            name="ck_media_analysis_media_type",
        ),
        CheckConstraint(
            "status IN ('completed', 'partial', 'failed')",
            name="ck_media_analysis_status",
        ),
        Index(
            "ix_media_analysis_project_visitor_created",
            "project_id",
            "visitor_id",
            "created_at",
        ),
        Index(
            "ix_media_analysis_project_status_created",
            "project_id",
            "status",
            "created_at",
        ),
    )
