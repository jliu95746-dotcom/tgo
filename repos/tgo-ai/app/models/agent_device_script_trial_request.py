"""Original preparation, dispatch fence and physical-stop observations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentDeviceScriptTrialRequest(BaseModel):
    __tablename__ = "ai_agent_device_script_trial_requests"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "actor_id", "request_id",
            name="uq_ai_script_trial_preparation_request",
        ),
        UniqueConstraint(
            "report_id", name="uq_ai_script_trial_request_report"
        ),
        CheckConstraint(
            "status IN ('preparing','ready','failed','cancelled')",
            name="ck_ai_script_trial_request_status",
        ),
        CheckConstraint(
            "stop_status IN ('none','requested','confirmed','finished')",
            name="ck_ai_script_trial_request_stop",
        ),
        CheckConstraint(
            "binding_revision >= 1",
            name="ck_ai_script_trial_request_revision",
        ),
        CheckConstraint(
            "command_digest IS NULL OR length(command_digest) = 64",
            name="ck_ai_script_trial_request_digest",
        ),
        CheckConstraint(
            "(status = 'ready' AND report_id IS NOT NULL AND "
            "completed_at IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'preparing' AND report_id IS NULL AND "
            "completed_at IS NULL AND error_code IS NULL AND "
            "dispatched_at IS NULL AND stop_status = 'none') OR "
            "(status IN ('failed','cancelled') AND report_id IS NULL AND "
            "completed_at IS NOT NULL AND error_code IS NOT NULL AND "
            "dispatched_at IS NULL)",
            name="ck_ai_script_trial_request_outcome",
        ),
        CheckConstraint(
            "(status = 'cancelled' AND error_code = 'cancelled' AND "
            "stop_status = 'confirmed' AND stop_requested_at IS NOT NULL) "
            "OR (status != 'cancelled' AND "
            "(error_code IS NULL OR error_code != 'cancelled'))",
            name="ck_ai_script_trial_request_cancel",
        ),
        CheckConstraint(
            "stop_status != 'requested' OR "
            "(dispatched_at IS NOT NULL AND stop_requested_at IS NOT NULL)",
            name="ck_ai_script_trial_request_pending_stop",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    actor_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), index=True
    )
    binding_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_device_script_bindings.id"), index=True
    )
    binding_revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[UUID] = mapped_column()
    command_digest: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    stop_status: Mapped[str] = mapped_column(String(16))
    report_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_agent_device_script_trials.id")
    )
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    stop_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[str | None] = mapped_column(String(32))
