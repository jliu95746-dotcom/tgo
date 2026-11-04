"""Reserve a case request before dispatch and retain its encrypted proof."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentDeviceScriptEvidence(BaseModel):
    __tablename__ = "ai_agent_device_script_evidence"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_script_evidence_request",
        ),
        UniqueConstraint("report_id", name="uq_ai_script_evidence_report"),
        CheckConstraint(
            "length(command_digest) = 64 AND length(case_digest) = 64 AND "
            "(result_digest IS NULL OR length(result_digest) = 64)",
            name="ck_ai_script_evidence_digests",
        ),
        CheckConstraint(
            "(captured_at IS NULL AND result_digest IS NULL AND "
            "ciphertext IS NULL) OR (captured_at IS NOT NULL AND "
            "report_id IS NOT NULL AND result_digest IS NOT NULL AND "
            "((ciphertext IS NOT NULL AND cleared_at IS NULL AND "
            "length(ciphertext) <= 200000) OR "
            "(ciphertext IS NULL AND cleared_at IS NOT NULL)))",
            name="ck_ai_script_evidence_body",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    actor_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), index=True
    )
    request_id: Mapped[UUID] = mapped_column()
    case_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_device_script_cases.id"), index=True
    )
    case_digest: Mapped[str] = mapped_column(String(64))
    command_digest: Mapped[str] = mapped_column(String(64))
    report_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_agent_device_script_trials.id")
    )
    result_digest: Mapped[str | None] = mapped_column(String(64))
    ciphertext: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
