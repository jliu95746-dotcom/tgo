"""Timer configuration and immutable replies, owned by Workflow."""

from datetime import datetime
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkflowSchedule(Base):
    __tablename__ = "wf_workflow_schedules"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "workflow_id", name="uq_wf_schedule_owner"
        ),
        CheckConstraint("revision >= 1", name="ck_wf_schedule_revision"),
        Index("ix_wf_schedule_due", "enabled", "next_run_at"),
        CheckConstraint(
            "(enabled AND next_run_at IS NOT NULL) OR "
            "(NOT enabled AND next_run_at IS NULL)",
            name="ck_wf_schedule_due",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("wf_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    release_id: Mapped[str] = mapped_column(String(128), nullable=False)
    release_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(512), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    suspension_reason: Mapped[str | None] = mapped_column(String(64))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkflowScheduleCommand(Base):
    __tablename__ = "wf_workflow_schedule_commands"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('save', 'disable')",
            name="ck_wf_schedule_command_operation",
        ),
    )

    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("wf_workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_staff_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_snapshot: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
