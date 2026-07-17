"""Persisted intent classification for one inbound platform message."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MessageIntentResult(Base):
    """Current policy-enforced intent result for a source message."""

    __tablename__ = "api_message_intent_results"

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
    media_analysis_result_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_media_analysis_results.id", ondelete="SET NULL"),
        nullable=True,
    )
    intent: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    entities: Mapped[dict[str, str | None]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    recommended_route: Mapped[str] = mapped_column(String(30), nullable=False)
    need_human: Mapped[bool] = mapped_column(Boolean, nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    classification_source: Mapped[str] = mapped_column(String(20), nullable=False)
    classifier_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
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
            name="uq_message_intent_source_message",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_message_intent_confidence",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')",
            name="ck_message_intent_risk_level",
        ),
        CheckConstraint(
            "recommended_route IN "
            "('auto_reply', 'read_only_tool', 'clarify', 'human_handoff')",
            name="ck_message_intent_route",
        ),
        CheckConstraint(
            "classification_source IN ('model', 'rule', 'fail_closed')",
            name="ck_message_intent_classification_source",
        ),
        Index(
            "ix_message_intent_project_visitor_created",
            "project_id",
            "visitor_id",
            "created_at",
        ),
        Index(
            "ix_message_intent_project_intent_created",
            "project_id",
            "intent",
            "created_at",
        ),
        Index(
            "ix_message_intent_project_handoff_created",
            "project_id",
            "need_human",
            "created_at",
        ),
    )
