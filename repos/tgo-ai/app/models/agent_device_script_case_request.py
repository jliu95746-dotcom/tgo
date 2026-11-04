"""Metadata-only save ledger; terminal rows fence delayed submissions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class ScriptCaseSaveRequest(BaseModel):
    __tablename__ = "ai_agent_device_script_case_saves"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_script_case_save_request",
        ),
        CheckConstraint(
            "status IN ('preparing','saved','failed','cancelled')",
            name="ck_ai_script_case_save_status",
        ),
        CheckConstraint(
            "binding_revision >= 1 AND expected_revision >= 0",
            name="ck_ai_script_case_save_revision",
        ),
        CheckConstraint(
            "(case_id IS NULL AND expected_revision = 0) OR "
            "(case_id IS NOT NULL AND expected_revision > 0)",
            name="ck_ai_script_case_save_root",
        ),
        CheckConstraint(
            "command_digest IS NULL OR length(command_digest)=64",
            name="ck_ai_script_case_save_digest",
        ),
        CheckConstraint(
            "(status='preparing' AND version_id IS NULL AND "
            "completed_at IS NULL AND error_code IS NULL) OR "
            "(status='saved' AND version_id IS NOT NULL AND "
            "completed_at IS NOT NULL AND error_code IS NULL) OR "
            "(status='failed' AND version_id IS NULL AND "
            "completed_at IS NOT NULL AND error_code IS NOT NULL AND "
            "error_code IN "
            "('preparation_failed','preparation_expired')) OR "
            "(status='cancelled' AND version_id IS NULL AND "
            "completed_at IS NOT NULL AND error_code IS NOT NULL AND "
            "error_code='cancelled')",
            name="ck_ai_script_case_save_outcome",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    actor_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), index=True
    )
    request_id: Mapped[UUID] = mapped_column()
    binding_id: Mapped[UUID] = mapped_column()
    binding_revision: Mapped[int] = mapped_column(Integer)
    case_id: Mapped[UUID | None] = mapped_column()
    expected_revision: Mapped[int] = mapped_column(Integer)
    command_digest: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_agent_device_script_cases.id")
    )
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[str | None] = mapped_column(String(32))
