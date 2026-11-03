"""Private, payload-free tool event attached to an AI execution attempt."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy import UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIExecutionToolEvent(Base):
    """Retain the event order without copying tool arguments or results."""

    __tablename__ = "api_ai_execution_tool_events"
    __table_args__ = (
        CheckConstraint("sequence_no > 0", name="ck_ai_tool_event_sequence"),
        CheckConstraint(
            "status IN ('started', 'completed', 'failed')",
            name="ck_ai_tool_event_status",
        ),
        UniqueConstraint(
            "execution_id", "sequence_no", name="uq_ai_tool_event_sequence",
        ),
        Index(
            "ix_ai_tool_event_project_execution",
            "project_id", "execution_id", "sequence_no",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False,
    )
    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_ai_execution_attempts.execution_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
