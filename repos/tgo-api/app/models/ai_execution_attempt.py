"""One immutable AI execution identity for an inbound-message attempt."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIExecutionAttempt(Base):
    """Keep the model run ID after a failed message is retried."""

    __tablename__ = "api_ai_execution_attempts"
    __table_args__ = (
        UniqueConstraint(
            "interaction_run_id",
            "response_client_msg_no",
            name="uq_ai_execution_attempt_reply",
        ),
        Index(
            "ix_ai_execution_attempt_project_interaction",
            "project_id",
            "interaction_run_id",
            "started_at",
        ),
    )

    execution_id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    interaction_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_ai_interaction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    response_client_msg_no: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
