"""Private, independent AI candidate for a verified customer message."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
    Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HiddenShadowRun(Base):
    __tablename__ = "api_hidden_shadow_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "platform_id", "source_message_id",
            name="uq_hidden_shadow_source",
        ),
        CheckConstraint(
            "status IN ('running','ready','blocked','failed')",
            name="ck_hidden_shadow_status",
        ),
        CheckConstraint(
            "attempt_count >= 0", name="ck_hidden_shadow_attempts",
        ),
        Index("ix_hidden_shadow_project_created", "project_id", "created_at"),
        Index("ix_hidden_shadow_project_visitor", "project_id", "visitor_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False,
    )
    platform_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_platforms.id", ondelete="CASCADE"), nullable=False,
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"), nullable=False,
    )
    source_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_message_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    source_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    source_content_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    agent_id: Mapped[UUID | None] = mapped_column(nullable=True)
    generation_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    lease_token: Mapped[UUID | None] = mapped_column(nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    knowledge_status: Mapped[str | None] = mapped_column(
        String(16),
    )
    knowledge_evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(255))
    configured_prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    ai_execution_id: Mapped[UUID | None] = mapped_column(nullable=True)
    answer: Mapped[str | None] = mapped_column(Text)
    answer_sha256: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    redacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
