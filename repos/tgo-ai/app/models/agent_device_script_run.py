"""Original approved business requests and metadata-only recovery snapshots."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentDeviceScriptRun(BaseModel):
    __tablename__ = "ai_agent_device_script_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_script_run_request",
        ),
        UniqueConstraint(
            "execution_id", name="uq_ai_script_run_execution"
        ),
        CheckConstraint(
            "status IN ('preparing','unknown','succeeded',"
            "'failed','cancelled')",
            name="ck_ai_script_run_status",
        ),
        CheckConstraint(
            "deadline_at > created_at AND "
            "((status IN ('succeeded','failed','cancelled') "
            "AND completed_at IS NOT NULL) OR "
            "(status IN ('preparing','unknown') AND completed_at IS NULL))",
            name="ck_ai_script_run_timeline",
        ),
        CheckConstraint(
            "(reference_json IS NULL AND reference_digest IS NULL "
            "AND execution_id IS NULL) OR (reference_json IS NOT NULL "
            "AND reference_digest IS NOT NULL "
            "AND length(reference_json) <= 131072 "
            "AND length(reference_digest) = 64 AND execution_id IS NOT NULL)",
            name="ck_ai_script_run_reference",
        ),
        CheckConstraint(
            "status NOT IN ('unknown','succeeded') OR "
            "(execution_id IS NOT NULL AND dispatch_started_at IS NOT NULL)",
            name="ck_ai_script_run_dispatch",
        ),
        CheckConstraint(
            "(command_digest IS NULL OR length(command_digest) = 64) AND "
            "(result_digest IS NULL OR length(result_digest) = 64)",
            name="ck_ai_script_run_digests",
        ),
        CheckConstraint(
            "(agent_execution_id IS NULL AND source_json IS NULL "
            "AND source_digest IS NULL) OR (agent_execution_id IS NOT NULL "
            "AND source_json IS NOT NULL AND length(source_json) <= 4096 "
            "AND source_digest IS NOT NULL AND length(source_digest) = 64)",
            name="ck_ai_script_run_source",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    actor_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"),
        index=True,
    )
    request_id: Mapped[UUID] = mapped_column()
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_device_script_trials.id"),
    )
    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_device_script_reviews.id"),
    )
    command_digest: Mapped[str | None] = mapped_column(String(64))
    agent_execution_id: Mapped[UUID | None] = mapped_column(index=True)
    source_json: Mapped[str | None] = mapped_column(Text)
    source_digest: Mapped[str | None] = mapped_column(String(64))
    execution_id: Mapped[UUID | None] = mapped_column()
    reference_json: Mapped[str | None] = mapped_column(Text)
    reference_digest: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(32))
    result_digest: Mapped[str | None] = mapped_column(String(64))
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )
    dispatch_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    stop_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
