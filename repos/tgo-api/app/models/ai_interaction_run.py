"""Persistent identity and lifecycle for one inbound-message AI interaction."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIInteractionRunStatus(str, Enum):
    """Lifecycle of a globally idempotent AI interaction."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AIInteractionRun(Base):
    """One AI run claimed by a channel-neutral upstream message identity."""

    __tablename__ = "api_ai_interaction_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "platform_id",
            "channel_id",
            "channel_type",
            "source_message_id",
            name="uq_ai_interaction_global_message",
        ),
        UniqueConstraint(
            "response_client_msg_no",
            name="uq_ai_interaction_response_client_msg_no",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_ai_interaction_run_status",
        ),
    )

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
    channel_id: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_type: Mapped[int] = mapped_column(Integer, nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    response_client_msg_no: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=AIInteractionRunStatus.RUNNING.value,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
        onupdate=datetime.utcnow,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
