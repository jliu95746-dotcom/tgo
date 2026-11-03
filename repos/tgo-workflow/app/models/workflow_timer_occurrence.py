"""One durable source fact per schedule revision and UTC due minute."""

from datetime import datetime
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class WorkflowTimerOccurrence(Base):
    __tablename__ = "wf_timer_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "schedule_id", "revision", "scheduled_at", name="uq_wf_timer_due"
        ),
        CheckConstraint(
            "status IN ('pending','queued','skipped','cancelled','blocked')",
            name="ck_wf_timer_status",
        ),
        CheckConstraint(
            "attempts >= 0 AND revision >= 1", name="ck_wf_timer_attempts"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(String, nullable=False)
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("wf_workflow_schedules.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    actor_staff_id: Mapped[str] = mapped_column(String(36), nullable=False)
    release_id: Mapped[str] = mapped_column(String(128), nullable=False)
    release_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    last_failure_code: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    execution_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
