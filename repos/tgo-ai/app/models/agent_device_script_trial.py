"""Durable test dispatch identity and comparisons without business bodies."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentDeviceScriptTrial(BaseModel):
    __tablename__ = "ai_agent_device_script_trials"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_script_trial_request",
        ),
        UniqueConstraint("execution_id", name="uq_ai_script_trial_execution"),
        CheckConstraint(
            "status IN ('unknown','matched','mismatched','failed')",
            name="ck_ai_script_trial_status",
        ),
        CheckConstraint(
            "binding_revision >= 1", name="ck_ai_script_trial_revision"
        ),
        CheckConstraint(
            "remaining_runs IS NULL OR remaining_runs BETWEEN 0 AND 100",
            name="ck_ai_script_trial_remaining",
        ),
        CheckConstraint(
            "length(reference_json) <= 131072",
            name="ck_ai_script_trial_reference",
        ),
        CheckConstraint(
            "length(command_digest) = 64 AND length(expected_digest) = 64 AND "
            "length(reference_digest) = 64 AND "
            "length(policy_digest) = 64 AND length(grant_digest) = 64 AND "
            "(result_digest IS NULL OR length(result_digest) = 64)",
            name="ck_ai_script_trial_digests",
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
    device_id: Mapped[UUID] = mapped_column()
    execution_id: Mapped[UUID] = mapped_column()
    command_digest: Mapped[str] = mapped_column(String(64))
    expected_digest: Mapped[str] = mapped_column(String(64))
    policy_digest: Mapped[str] = mapped_column(String(64))
    grant_digest: Mapped[str] = mapped_column(String(64))
    result_digest: Mapped[str | None] = mapped_column(String(64))
    reference_json: Mapped[str] = mapped_column(Text)
    reference_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(32))
    remaining_runs: Mapped[int | None] = mapped_column(Integer)
    dispatch_claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )
    dispatch_deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
